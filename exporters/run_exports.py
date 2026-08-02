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

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--bootstrap", action="store_true")
    a = ap.parse_args()
    targets = select(a.only)
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
