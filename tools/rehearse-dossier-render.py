#!/usr/bin/env python3
"""ORDER 36 step 5 rehearsal driver: run the dossier exporters against a Neon
BRANCH, staging-only, never production and never the vault.

Run it through db-tap so the branch DSN never appears in a shell command:

    .venv/bin/python tools/db-tap.py --branch rehearse-0028 \
        run tools/rehearse-dossier-render.py [--only dossier] [--bootstrap]

It borrows DATABASE_URL (which db-tap sets to the branch) as the exporter
credential for this process only. CARR_EXPORT_LIVE is forced off: a rehearsal
must not be able to write the vault even by accident.
"""
import os
import sys

os.environ.pop("CARR_EXPORT_LIVE", None)
url = os.environ.get("DATABASE_URL")
if not url:
    sys.exit("no DATABASE_URL — run this through tools/db-tap.py --branch <name> run")
os.environ["CARR_DB_EXPORTER_URL"] = url

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from exporters.run_exports import main  # noqa: E402

if __name__ == "__main__":
    main()
