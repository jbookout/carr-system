#!/usr/bin/env python3
"""Prove the delivery tags actually install and actually select, on a disposable
PostgreSQL that carries the committed schema and every migration.

WHY AN ACCEPTANCE RUN AND NOT ONLY A SELFTEST. ops/rule-load-layer-check.py
proves the reviewed map is internally deliverable. It cannot prove that the
backfill writes what the map says, that the constraints refuse what the check
refuses, or that ops.rule_delivery_plan hands a session the set the council
designed. Those are properties of the DATABASE, and the only honest way to know
them is to run them against one (rule 937252fb: exercise the whole path before
saying a capability is live).

WHAT IT SEEDS. One active rule per id in the reviewed map, with a uuid whose
first eight characters ARE that short id, because that is how every surface in
this system derives a short id. No rule text is invented beyond a marker
statement: this proves plumbing, never doctrine.

    CARR_LOCAL_PG_DSN=postgresql://... ops/rule-delivery-local-pg-acceptance.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.rule_delivery_activation import EXPECTED_IDS, load_validated  # noqa:E402

REPO = Path(__file__).resolve().parent.parent
MAP = REPO / "ops" / "config" / "rule-enforcement-map.json"
PY = REPO / ".venv" / "bin" / "python"
MIGRATION_0363 = REPO / "migrations" / "0363_rule_delivery_activation_digest_repin.sql"
PRIOR_ACTIVATION_DIGEST = "4038e097f571f73499aee79b8c9e7b5bd3cea4ca0ba0f3847873e2f720106218"
CURRENT_ACTIVATION_DIGEST = "f7bf5726d329dd240434e51f7401fac9a977a3fb710636738f379f60f565f904"
ACTIVATION_TO_TEST_REF = (
    "ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; "
    "ops/rule-pack-preuse-reselection-selftest.py"
)
RETIRED_ACTIVATION_ID = "581cb3fe"

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if not ok:
        FAILURES.append(f"{label}{': ' + detail if detail else ''}")


def one(cur) -> tuple:
    """fetchone() that refuses None. A count query that returned no row at all is
    a broken query, not a zero, and reading [0] off None hides which it was."""
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("a count query returned no row; the acceptance cannot judge that")
    return row


def run(script: str, dsn: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(PY if PY.is_file() else sys.executable), str(REPO / script)],
                          env={**os.environ, "DATABASE_URL": dsn},
                          capture_output=True, text=True, cwd=REPO)


def uuid_for(short: str, tail: int = 1) -> str:
    return f"{short}-0000-4000-8000-{tail:012d}"


def main() -> int:
    dsn = os.environ.get("CARR_LOCAL_PG_DSN")
    if not dsn:
        print("rule-delivery-acceptance: CARR_LOCAL_PG_DSN required", file=sys.stderr)
        return 78
    data = json.loads(MAP.read_text())
    layers = data["rule_load_layers"]
    scope_by_id = {rid: scope for scope, ids in data["active_rule_ids"].items() for rid in ids}
    # Scope is durable rule state, not reviewed-map configuration. Deliberately
    # make one map-shared rule Dell-personal to prove the compiler reads
    # rule.personal_to and the audit checks the installed result.
    store_scope_by_id = dict(scope_by_id)
    synthetic_dell = next(rid for rid in data["active_rule_ids"]["shared"]
                          if rid not in EXPECTED_IDS)
    store_scope_by_id[synthetic_dell] = "dell"

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """insert into ops.rule_delivery_activation_target
                 (short_id,expected_scope,expected_pack,
                  from_control,from_enforcement_class,from_implementation_ref,
                  from_test_ref,to_control,to_enforcement_class,
                  to_implementation_ref,to_test_ref,map_digest)
               values
                 (%s,'shared','delegation-council',
                  'session_boot','surfacing',
                  'hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js',
                  'command:python3 hooks/gate-integrity.py --selftest',
                  'pack_delivery','stop_gate',
                  'hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py',
                  %s,%s)""",
            (RETIRED_ACTIVATION_ID, ACTIVATION_TO_TEST_REF, PRIOR_ACTIVATION_DIGEST),
        )
        check("the pre-0363 fixture restores the exact retired ninth target",
              cur.rowcount == 1)
        # 0363 must refuse a same-ID row whose reviewed activation preimage
        # drifted, and the failed transaction must not repin any digest.
        migration_0363 = MIGRATION_0363.read_text(encoding="utf-8")
        activation_victim = sorted(EXPECTED_IDS)[0]
        cur.execute(
            "update ops.rule_delivery_activation_target set map_digest=%s",
            (PRIOR_ACTIVATION_DIGEST,),
        )
        cur.execute(
            """update ops.rule_delivery_activation_target
                  set to_test_ref='drifted same-ID acceptance fixture'
                where short_id=%s""",
            (activation_victim,),
        )
        try:
            cur.execute(migration_0363, prepare=False)
            check("0363 refuses a drifted same-ID activation preimage", False,
                  "migration accepted the drifted row")
        except psycopg.Error as exc:
            check(
                "0363 refuses a drifted same-ID activation preimage",
                "expected the exact reviewed nine-row activation preimage" in str(exc),
                str(exc).splitlines()[0],
            )
        conn.rollback()
        cur.execute(
            """select count(*) from ops.rule_delivery_activation_target
                where map_digest=%s""",
            (PRIOR_ACTIVATION_DIGEST,),
        )
        check("a refused 0363 repin changes no target digest", one(cur)[0] == len(EXPECTED_IDS) + 1)
        cur.execute(
            "delete from ops.rule_delivery_activation_target where short_id=%s",
            (RETIRED_ACTIVATION_ID,),
        )
        check("the post-0363 fixture removes exactly the retired target",
              cur.rowcount == 1)
        cur.execute(
            """update ops.rule_delivery_activation_target
                  set map_digest=%s,
                      to_test_ref=%s
                where short_id=any(%s)""",
            (CURRENT_ACTIVATION_DIGEST, ACTIVATION_TO_TEST_REF, sorted(EXPECTED_IDS)),
        )

        check("the post-0363 fixture restores all eight current target digests",
              cur.rowcount == len(EXPECTED_IDS))
        cur.execute(
            """select count(*) from ops.rule_delivery_activation_target
                where map_digest=%s""",
            (CURRENT_ACTIVATION_DIGEST,),
        )
        check("the restored post-0363 fixture is the exact current eight",
              one(cur)[0] == len(EXPECTED_IDS))
        cur.execute("""insert into actor (slug,kind,display_name) values ('joe','human','Joe')
                       on conflict (slug) do nothing returning id""")
        cur.execute("select id from actor where slug='joe'")
        joe = one(cur)[0]
        cur.execute("""insert into actor (slug,kind,display_name) values ('dell','human','Dell')
                       on conflict (slug) do nothing returning id""")
        cur.execute("select id from actor where slug='dell'")
        dell = one(cur)[0]
        # THE SEED TURNS OFF THE RULE LIFECYCLE TRIGGERS, ON A THROWAWAY DATABASE,
        # AND SAYS SO. Activating a rule for real requires the whole Joe approval
        # chain migration 0228 built — an admission contract, installed controls,
        # an immutable approval receipt — and retiring one requires a retirement
        # receipt. That chain is exactly what
        # ops/atomic-rule-approval-local-pg-acceptance.py exists to prove, and
        # reproducing it 218 times here would test that chain a second time while
        # testing delivery not at all. What is under test in this file is whether
        # the tags install, select and refuse. Production's triggers are untouched;
        # this statement needs table ownership, which no application role holds.
        cur.execute("alter table rule disable trigger user")
        cur.execute("delete from ops.rule_load_layer")
        cur.execute("delete from ops.rule_pack")
        cur.execute("delete from rule where statement like 'delivery acceptance %'")
        for short, scope in sorted(store_scope_by_id.items()):
            cur.execute("""insert into rule (id,statement,taught_by,status,activated_by,
                                             personal_to)
                           values (%s,%s,%s,'active',%s,%s)
                           on conflict (id) do nothing""",
                        (uuid_for(short), f"delivery acceptance {short}", joe, joe,
                         joe if scope == "joe" else dell if scope == "dell" else None))

        # ── the backfill installs exactly what the map says ──────────────────
        result = run("tools/sync-rule-load-layers.py", dsn)
        check("the backfill runs clean", result.returncode == 0,
              result.stderr.strip()[-400:])
        cur.execute("select count(*) from ops.rule_load_layer")
        check("every reviewed tag landed", one(cur)[0] == len(layers))
        cur.execute("select count(*) from ops.rule_pack")
        check("every reviewed pack landed", one(cur)[0] == len(data["rule_packs"]))

        audit = run("ops/rule-delivery-audit.py", dsn)
        check("the delivery audit passes after the backfill", audit.returncode == 0,
              (audit.stdout + audit.stderr).strip()[-400:])

        # ── the selector hands back the set the council designed ─────────────
        cur.execute("select count(*) from ops.rule_delivery_plan('joe') where selected")
        layer0 = one(cur)[0]
        expected_layer0 = sum(1 for s, e in layers.items()
                              if e["load_layer"] == "layer0"
                              and store_scope_by_id[s] in ("shared", "joe"))
        check("an undeclared boot selects exactly Layer 0",
              layer0 == expected_layer0, f"{layer0} != {expected_layer0}")
        check("an undeclared boot is never empty", layer0 > 0)
        cur.execute("select count(*) from ops.rule_delivery_plan('joe')")
        check("the plan still reports every in-scope rule, which is what shadow "
              "mode compares against",
              one(cur)[0] == sum(store_scope_by_id[s] in ("shared", "joe") for s in layers))

        cur.execute("select count(*) from ops.rule_delivery_plan('dell')")
        check("Dell's plan contains shared plus Dell-personal rules only",
              one(cur)[0] == sum(store_scope_by_id[s] in ("shared", "dell") for s in layers))

        cur.execute("select count(*) from ops.rule_delivery_plan(null)")
        check("an unsponsored plan contains shared rules only",
              one(cur)[0] == sum(store_scope_by_id[s] == "shared" for s in layers))

        cur.execute("""select count(*) from ops.rule_delivery_plan('joe', array['engineering-git'])
                        where selected""")
        with_pack = one(cur)[0]
        check("declaring a pack adds rules and never removes any", with_pack > layer0)

        cur.execute("select count(*) from ops.rule_delivery_plan(null) where scope='joe'")
        check("an unsponsored runtime gets no partner's personal rules",
              one(cur)[0] == 0)
        cur.execute("select count(*) from ops.rule_delivery_plan('joe') where scope='dell'")
        check("Joe's plan contains no Dell-personal rules", one(cur)[0] == 0)
        cur.execute("select count(*) from ops.rule_delivery_plan('dell') where scope='joe'")
        check("Dell's plan contains no Joe-personal rules", one(cur)[0] == 0)

        cur.execute("select count(*) from ops.rule_pack_index() where rule_count = 0")
        check("no pack in the index is empty", one(cur)[0] == 0)
        cur.execute("select count(*) from ops.rule_pack_index()")
        check("the pack index names every pack", one(cur)[0] == len(data["rule_packs"]))

        cur.execute("select mode from ops.rule_delivery_policy")
        check("delivery starts in shadow mode", one(cur)[0] == "shadow")

        # A scope-only corruption used to pass both the backfill and audit.
        victim = synthetic_dell
        cur.execute("update ops.rule_load_layer set scope='shared' where short_id=%s", (victim,))
        audit = run("ops/rule-delivery-audit.py", dsn)
        check("the audit fails closed on a personal-scope mismatch",
              audit.returncode != 0 and "scope_mismatch=1" in audit.stdout,
              audit.stdout.strip())
        result = run("tools/sync-rule-load-layers.py", dsn)
        check("the reviewed backfill repairs a corrupted installed scope",
              result.returncode == 0, result.stderr.strip()[-300:])

        # ── the refusals are real ────────────────────────────────────────────
        for label, packs, expect_fail in (
                ("a wildcard pack is refused by the database", ["*"], True),
                ("an undefined pack is refused by the database", ["not-a-pack"], True)):
            try:
                cur.execute("""update ops.rule_load_layer set packs=%s
                                where load_layer='pack'
                                  and short_id=(select min(short_id) from ops.rule_load_layer
                                                 where load_layer='pack')""", (packs,))
                check(label, not expect_fail, "the write was accepted")
            except psycopg.Error:
                check(label, expect_fail)
        conn.rollback() if not conn.autocommit else None

        try:
            cur.execute("""update ops.rule_load_layer set packs=array['engineering-git']
                            where load_layer='layer0'
                              and short_id=(select min(short_id) from ops.rule_load_layer
                                             where load_layer='layer0')""")
            check("layer0 cannot be narrowed by a pack", False, "the write was accepted")
        except psycopg.Error:
            check("layer0 cannot be narrowed by a pack", True)

        # ── a rule taught after the map stops the backfill, it is not guessed ─
        cur.execute("""insert into rule (id,statement,taught_by,status,activated_by)
                       values (%s,'delivery acceptance newcomer',%s,'active',%s)""",
                    (uuid_for("fedcba98"), joe, joe))
        result = run("tools/sync-rule-load-layers.py", dsn)
        check("an untagged active rule refuses the backfill", result.returncode != 0)
        check("and the refusal names the rule", "fedcba98" in result.stderr,
              result.stderr.strip()[-200:])
        audit = run("ops/rule-delivery-audit.py", dsn)
        check("the audit reports the untagged rule rather than passing",
              audit.returncode != 0 and "untagged=1" in audit.stdout, audit.stdout.strip())

        # ── retiring a rule removes its tag rather than leaving dead law ─────
        cur.execute("update rule set status='retired' where id=%s", (uuid_for("fedcba98"),))
        victim = sorted(s for s, e in layers.items() if e["load_layer"] == "pack")[0]
        cur.execute("update rule set status='retired' where id=%s", (uuid_for(victim),))
        result = run("tools/sync-rule-load-layers.py", dsn)
        check("a tag for a retired rule refuses the backfill", result.returncode != 0)
        check("and names it", victim in result.stderr, result.stderr.strip()[-200:])
        cur.execute("update rule set status='active' where id=%s", (uuid_for(victim),))

        result = run("tools/sync-rule-load-layers.py", dsn)
        check("the backfill runs clean again once the store and map agree",
              result.returncode == 0, result.stderr.strip()[-300:])

        # ── policy and eight controls move in one guarded transaction ─────────
        admission = run("tools/sync-rule-admission.py", dsn)
        check("the admission sync builds the eight-control preimage",
              admission.returncode == 0, admission.stderr.strip()[-500:])
        digest = load_validated()[1]["base_map_sha256"]
        try:
            cur.execute("""select * from ops.set_rule_delivery_mode(
                           'enforced','local-pg-acceptance','seven-day evidence fixture',%s)""",
                        (digest,))
        except psycopg.Error as exc:
            # psycopg appends a PL/pgSQL CONTEXT line after the useful refusal.
            # The local runner reports only the final line, so preserve the
            # database's first message without echoing a query or DSN.
            raise RuntimeError(
                "rule-delivery cutover refused: " + str(exc).splitlines()[0]
            ) from None
        cutover = one(cur)
        check("cutover reports the exact eight", cutover[0] == "enforced" and cutover[1] == len(EXPECTED_IDS))
        cur.execute("select mode from ops.rule_delivery_policy")
        check("cutover flips policy to enforced", one(cur)[0] == "enforced")
        cur.execute("""select count(*) from ops.rule_enforcement_point ep
                       join rule r on r.id=ep.rule_id
                      where left(r.id::text,8)=any(%s) and ep.control_key='pack_delivery'
                        and ep.enforcement_class='stop_gate' and ep.installed""",
                    (sorted(EXPECTED_IDS),))
        check("cutover installs pack_delivery/stop_gate on all eight", one(cur)[0] == len(EXPECTED_IDS))

        admission = run("tools/sync-rule-admission.py", dsn)
        check("a future admission sync is policy-aware", admission.returncode == 0,
              admission.stderr.strip()[-500:])
        cur.execute("""select count(*) from ops.rule_enforcement_point ep
                       join rule r on r.id=ep.rule_id
                      where left(r.id::text,8)=any(%s) and ep.control_key='pack_delivery'
                        and ep.enforcement_class='stop_gate'""", (sorted(EXPECTED_IDS),))
        check("an enforced sync does not revert the eight controls", one(cur)[0] == len(EXPECTED_IDS))

        try:
            cur.execute("update ops.rule_delivery_policy set mode='shadow' where singleton")
            check("a direct policy-only update is refused", False, "update was accepted")
        except psycopg.Error as exc:
            check("a direct policy-only update is refused",
                  "use ops.set_rule_delivery_mode" in str(exc), str(exc))

        cur.execute("""select * from ops.set_rule_delivery_mode(
                       'shadow','local-pg-acceptance','rollback fixture',%s)""", (digest,))
        rollback = one(cur)
        check("rollback reports the exact eight", rollback[0] == "shadow" and rollback[1] == len(EXPECTED_IDS))
        cur.execute("""select count(*) from ops.rule_enforcement_point ep
                       join rule r on r.id=ep.rule_id
                      where left(r.id::text,8)=any(%s) and ep.control_key='session_boot'
                        and ep.enforcement_class='surfacing'""", (sorted(EXPECTED_IDS),))
        check("rollback restores all eight session_boot rows", one(cur)[0] == len(EXPECTED_IDS))
        cur.execute("select count(*) from ops.rule_delivery_activation_receipt")
        check("cutover and rollback each leave an append-only receipt", one(cur)[0] == 2)

        try:
            cur.execute("""select * from ops.set_rule_delivery_mode(
                           'enforced','local-pg-acceptance','wrong digest',%s)""",
                        ("0" * 64,))
            check("a stale map digest refuses cutover", False, "cutover was accepted")
        except psycopg.Error as exc:
            check("a stale map digest refuses cutover", "digest preimage" in str(exc), str(exc))
        cur.execute("select mode from ops.rule_delivery_policy")
        check("a refused cutover leaves policy in shadow", one(cur)[0] == "shadow")
        cur.execute("alter table rule enable trigger user")

    if FAILURES:
        print("rule-delivery-acceptance: FAIL", file=sys.stderr)
        for line in FAILURES:
            print(f"  {line}", file=sys.stderr)
        return 1
    print("rule-delivery-acceptance: delivery tags install, select and refuse as reviewed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
