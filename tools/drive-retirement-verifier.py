#!/usr/bin/env python3
"""drive-retirement-verifier.py — the record-layer verifier the static preflight
says it cannot be.

ops/drive-retirement-readiness-gate.py refuses to close Phase 4 on inventory
alone, in its own words: it "cannot resolve immutable repoint receipts, recovery
receipts, or Joe's authority receipt", and it deliberately has no --evidence
argument because JSON supplied by a caller is not a receipt. This tool is the
other half. It resolves those three facts from the record layer, where each one
is a row the caller could not have written.

WHAT IT RESOLVES, AND WHY EACH IS NOT A CLAIM:

  A reader was repointed — a write receipt whose digest the DATABASE recomputed
  from a frozen evidence row. Not a boolean, not a filename.

  Recovery was exercised — a second, DIFFERENT proven receipt. One receipt
  cannot make two claims.

  The partner approved it — an acceptance row that only the authority identity
  can create and that no machine actor can create at all.

READ ONLY. Opens with default_transaction_read_only=on, reads counts, never
prints the connection string, and reuses db-tap.py's DSN function rather than
handling credentials itself.

Exit codes, so this can gate a retirement:

  0  ready: every operational dependency retired on two proven receipts, with an
     authority acceptance on record
  0  not deployed: the retirement surface is absent, reported as such
  1  not ready: something specific is missing, and it says which

Usage:
  .venv/bin/python tools/drive-retirement-verifier.py
  .venv/bin/python tools/drive-retirement-verifier.py --project staging
"""
import argparse
import os
import sys

try:
    import psycopg
except ImportError:
    sys.exit("psycopg is not importable; run this with the repo's .venv interpreter")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    # NO DEFAULT TARGET. This argument used to default to "production", so a
    # run with CARR_QUALIFICATION_DSN unset and no --project opened a
    # PRODUCTION connection -- which is exactly what happened during the review
    # that found this, and the reason it is a defect rather than a preference.
    # The tool is read-only, so nothing was harmed; the point is that reaching
    # production must be something someone TYPED, never what happens when they
    # typed nothing. Naming --project production still works and still means it.
    ap.add_argument("--project", default=None,
                    help="deployment to resolve a DSN for; REQUIRED unless "
                         "CARR_QUALIFICATION_DSN names a target")
    ap.add_argument("--branch", default=None)
    args = ap.parse_args()

    # See tools/phase4-qualification.py: the same seam, for the same reason —
    # this verdict must be provable against a disposable cluster, because
    # production cannot be put into the failing states on purpose.
    url = os.environ.get("CARR_QUALIFICATION_DSN")
    target_label = args.project if not url else "LOCAL (CARR_QUALIFICATION_DSN)"
    if not url and not args.project:
        print("REFUSING TO GUESS A TARGET: neither CARR_QUALIFICATION_DSN nor "
              "--project was given.\n  --project production  reaches production, "
              "deliberately.\n  CARR_QUALIFICATION_DSN=...  points at a disposable "
              "local cluster.\nThere is no default, on purpose.", file=sys.stderr)
        return 2
    if not url:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "dbtap", os.path.join(os.path.dirname(os.path.abspath(__file__)), "db-tap.py"))
        dbtap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dbtap)
        url = dbtap.dsn(branch=args.branch, project=args.project)

    with psycopg.connect(url, options="-c default_transaction_read_only=on") as conn:
        with conn.cursor() as cur:
            cur.execute("""select count(*) from information_schema.tables
                            where table_schema='ops' and table_name='drive_retirement'""")
            if cur.fetchone()[0] != 1:
                print(f"target : {target_label}")
                print("\nNOT DEPLOYED: the retirement surface does not exist here, so "
                      "nothing\n              can be verified. That is a legitimate "
                      "pre-deploy state.")
                return 0

            cur.execute("""select operational_total, retired_total, remaining,
                                  has_authority, ready
                             from ops.drive_retirement_readiness()""")
            total, retired, remaining, has_auth, ready = cur.fetchone()

            print(f"target                     : {target_label}")
            print(f"operational dependencies   : {total}")
            print(f"retired with two receipts  : {retired}")
            print(f"remaining                  : {remaining}")
            print(f"authority acceptance       : {'present' if has_auth else 'ABSENT'}")

            # Anything retired on a receipt that is no longer proven would be a
            # contradiction the trigger should have prevented; check anyway,
            # because a verifier that only re-reads the summary it is verifying
            # is not a verifier.
            # LIVE ROWS ONLY. A retirement that was withdrawn is a mistake
            # somebody corrected on the record; re-checking its backing would
            # report the correction as a problem.
            live = """ and not exists (select 1 from ops.drive_retirement_withdrawal w
                                         where w.drive_retirement_id = r.id)"""
            cur.execute("""select count(*) from ops.drive_retirement r
                             join ops.write_receipt p on p.id = r.repoint_receipt_id
                             join ops.write_receipt v on v.id = r.recovery_receipt_id
                            where (not p.is_proven or not v.is_proven)""" + live)
            unproven_backing = cur.fetchone()[0]
            cur.execute("""select count(*) from ops.drive_retirement r
                            where r.repoint_receipt_id = r.recovery_receipt_id""" + live)
            same_receipt = cur.fetchone()[0]
            # RE-DERIVE THE GATE'S OWN CLAUSES rather than trusting the trigger
            # that enforced them. A verifier that only re-reads the summary it
            # is verifying is not a verifier, and the same argument applies to
            # re-reading a trigger's verdict.
            cur.execute("""select count(*) from ops.drive_retirement r
                             join ops.write_receipt p on p.id = r.repoint_receipt_id
                             join ops.write_receipt v on v.id = r.recovery_receipt_id
                            where (p.subject_type <> 'drive_dependency'
                                   or p.subject_id <> r.drive_dependency_id
                                   or v.subject_type <> 'drive_dependency'
                                   or v.subject_id <> r.drive_dependency_id)""" + live)
            unnamed = cur.fetchone()[0]
            cur.execute("""select count(*) from ops.drive_retirement r
                             join ops.write_receipt p on p.id = r.repoint_receipt_id
                             join ops.write_receipt v on v.id = r.recovery_receipt_id
                            where (p.tool_call_idempotency_key = v.tool_call_idempotency_key
                                   or p.material_digest = v.material_digest
                                   or v.prior_digest <> p.material_digest)""" + live)
            hollow_pairs = cur.fetchone()[0]
            cur.execute("select count(*) from ops.drive_retirement_withdrawal")
            withdrawn = cur.fetchone()[0]
            print(f"retirements on unproven    : {unproven_backing}")
            print(f"retirements reusing one    : {same_receipt}")
            print(f"receipts not naming the dep: {unnamed}")
            print(f"pairs asserting one thing  : {hollow_pairs}")
            print(f"withdrawn retirements      : {withdrawn}")

    problems = []
    if total == 0:
        problems.append("no operational Drive dependencies are on record; nothing "
                        "proven about nothing is not proof")
    if unnamed:
        problems.append(f"{unnamed} live retirements rest on receipts that do not name "
                        "the dependency they retired")
    if hollow_pairs:
        problems.append(f"{hollow_pairs} live retirements rest on two receipts that share "
                        "a call, assert the same material, or are not causally linked, so "
                        "one piece of work is being counted as two")
    if remaining and remaining > 0:
        problems.append(f"{remaining} operational dependencies are not retired")
    if not has_auth:
        problems.append("no authority acceptance is on record")
    if unproven_backing:
        problems.append(f"{unproven_backing} retirements cite a receipt that is not proven")
    if same_receipt:
        problems.append(f"{same_receipt} retirements reuse one receipt for both claims")

    print()
    if problems:
        print("VERDICT: NOT READY")
        for p in problems:
            print(f"  - {p}")
        return 1
    if not ready:
        print("VERDICT: NOT READY — the readiness function says no and this tool "
              "cannot say why.\n         That disagreement is itself the finding.")
        return 1
    print("VERDICT: READY — every operational dependency retired on two distinct "
          "proven\n         receipts, with an authority acceptance on record")
    return 0


if __name__ == "__main__":
    sys.exit(main())
