"""Run the Wave 1 exporters. Usage:
  python -m exporters.run_exports [--only <target>] [--bootstrap]
Staging by default; CARR_EXPORT_LIVE=1 activates vault paths (cutover only).
"""
import argparse, sys
from .common import run_export
from .targets import TARGETS

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--bootstrap", action="store_true")
    a = ap.parse_args()
    targets = {a.only: TARGETS[a.only]} if a.only else TARGETS
    ok = all(run_export(k, rel, fn, bootstrap=a.bootstrap) for k, (rel, fn) in targets.items())
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
