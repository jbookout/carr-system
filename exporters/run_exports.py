"""Run the Wave 1 exporters. Usage:
  python -m exporters.run_exports [--only <target>] [--bootstrap]
Staging by default; CARR_EXPORT_LIVE=1 activates vault paths (cutover only).
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

def md_renders_disabled():
    """THE CUTOFF FLAG (doctrine-store P5, decisions 20dfdfcc + 82a2fb62):
    when either server-side cutoff state is true, every .md target is skipped.
    ``retiring`` is the recoverable transition: it first blocks a concurrent
    export from recreating a file while the cutoff stages it; ``retired`` is the
    durable completed state. Non-md targets remain untouched. Fails OPEN on a
    read error so a dead config lookup can never silently kill the render fleet
    before cutover."""
    # Local stage/finalized sentinel is the fail-closed mirror.  The database is
    # durable truth, but if its read fails while files are staged, reopening the
    # exporter would recreate the retired surface.
    try:
        from lib.doctrine_cutoff_state import markdown_writes_blocked
        if markdown_writes_blocked():
            return True
    except Exception:
        # An existing but unreadable sentinel is handled fail-closed by the
        # helper. Import failure before rollout keeps pre-cutoff behaviour.
        pass
    try:
        from .common import connect
        with connect() as conn, conn.cursor() as cur:
            cur.execute("select key, value #>> '{}' from system_config "
                        "where key in ('doctrine.md_renders_retiring', "
                        "'doctrine.md_renders_retired')")
            return any(str(value).lower() == "true" for _key, value in cur.fetchall())
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--bootstrap", action="store_true")
    a = ap.parse_args()
    targets = select(a.only)
    if md_renders_disabled():
        dropped = [k for k, (rel, _fn) in targets.items() if rel.lower().endswith(".md")]
        for k in dropped:
            print(f"[{k}] DISABLED — md renders ended at the doctrine-store cutoff; "
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
