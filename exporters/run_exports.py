"""Run the Wave 1 exporters. Usage:
  python -m exporters.run_exports [--only <target>] [--bootstrap]
Draft by default; CARR_EXPORT_LIVE=1 activates vault paths (cutover only).
"""
import argparse, sys
from .common import run_export
from .targets import TARGETS

def select(only):
    """Exact target key, else every key with that prefix.

    The prefix form exists so `--only compiled-rules` refreshes both the shared
    and the personal rules file in one call — that is the command a session runs
    after a teach, and having to remember two target names would guarantee one of
    them goes stale.
    """
    if only is None:
        return TARGETS
    if only in TARGETS:
        return {only: TARGETS[only]}
    hits = {k: v for k, v in TARGETS.items() if k.startswith(only)}
    if not hits:
        sys.exit(f"no target matches '{only}'. known: {', '.join(sorted(TARGETS))}")
    return hits

def md_renders_retired():
    """THE CUTOFF FLAG (doctrine-store P5, decisions 20dfdfcc + 82a2fb62):
    when system_config doctrine.md_renders_retired = 'true', every .md target
    is skipped — the store serves doctrine and records; markdown projections
    are over. Non-md targets (xlsx working sheets, json feeds, html boards)
    are untouched. Fails OPEN (renders keep rendering) on any read error so a
    dead config lookup can never silently kill the render fleet pre-cutoff."""
    try:
        from .common import connect
        with connect() as conn, conn.cursor() as cur:
            cur.execute("select value #>> '{}' from system_config "
                        "where key = 'doctrine.md_renders_retired'")
            row = cur.fetchone()
            return bool(row) and str(row[0]).lower() == "true"
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--bootstrap", action="store_true")
    a = ap.parse_args()
    targets = select(a.only)
    if md_renders_retired():
        dropped = [k for k, (rel, _fn) in targets.items() if rel.lower().endswith(".md")]
        for k in dropped:
            print(f"[{k}] RETIRED — md renders ended at the doctrine-store cutoff; "
                  f"the store serves this (standing-context / read-doctrine / catch-me-up)")
        targets = {k: v for k, v in targets.items() if k not in dropped}
        if not targets:
            sys.exit(0)
    # NOT `all(...)`: it short-circuits, so ONE failing target silently cancels every
    # target after it in dict order. That is exactly what bit on 2026-08-02 — an
    # unbootstrapped decision-history target aborted the whole nightly export sweep and
    # five generated files went stale, while the chain reported only the first failure.
    # Run every target, then fail if any did.
    results = [run_export(k, rel, fn, bootstrap=a.bootstrap) for k, (rel, fn) in targets.items()]
    failed = [k for k, r in zip(targets, results) if not r]
    if failed:
        print(f"\n{len(failed)} of {len(results)} target(s) FAILED: {', '.join(failed)}")
    sys.exit(0 if not failed else 1)

if __name__ == "__main__":
    main()
