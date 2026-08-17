#!/usr/bin/env python3
"""Hermetic regression checks for the Control Plane provisioning preflight."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
PREFLIGHT = REPO / "ops" / "control-plane-provisioning-preflight.py"
CONFIG = REPO / "ops" / "config" / "control-plane-provisioning.v1.json"
FAILED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(("  ok    " if condition else "  FAIL  ") + label
          + (f" — {detail}" if detail else ""))
    if not condition:
        FAILED.append(label)


def run(config: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as tmp:
        candidate = Path(tmp) / "preflight.json"
        candidate.write_text(json.dumps(config), encoding="utf-8")
        return subprocess.run([sys.executable, str(PREFLIGHT), "--config", str(candidate)],
                              cwd=REPO, text=True, capture_output=True, check=False)


def main() -> int:
    print("control-plane-provisioning-preflight-selftest — declarations fail closed\n")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))

    accepted = run(config)
    check("declared external boundary contract passes without credential values",
          accepted.returncode == 0, accepted.stderr or accepted.stdout)

    missing_canary_name = json.loads(json.dumps(config))
    missing_canary_name["deterministic_canaries"]["required_names"].pop()
    rejected = run(missing_canary_name)
    check("canary provisioning refuses an incomplete isolated destination contract",
          rejected.returncode != 0 and "deterministic_canaries.required_names" in rejected.stdout,
          rejected.stdout or rejected.stderr)

    broad_canary_scope = json.loads(json.dumps(config))
    broad_canary_scope["deterministic_canaries"]["scope"] = "live destinations allowed"
    rejected = run(broad_canary_scope)
    check("canary provisioning refuses a live-equivalent scope declaration",
          rejected.returncode != 0 and "deterministic_canaries.scope" in rejected.stdout,
          rejected.stdout or rejected.stderr)

    ci = (REPO / "ops" / "ci.sh").read_text(encoding="utf-8")
    check("the standard CI gate class discovers this selftest",
          "for t in ops/*-selftest.py tools/test-*.py" in ci)

    static_preflight = PREFLIGHT.read_text(encoding="utf-8")
    check("static provisioning binds the positive authority-runtime probe",
          "control-plane-authority-runtime-preflight.py" in static_preflight
          and "authority runtime identity probe" in static_preflight)

    missing_authority = json.loads(json.dumps(config))
    del missing_authority["authority"]["environment_variables"]["dell"]
    rejected = run(missing_authority)
    check("missing Dell authority environment declaration refuses",
          rejected.returncode != 0 and "authority.environment_variables" in rejected.stdout,
          rejected.stdout or rejected.stderr)

    forged_role = json.loads(json.dumps(config))
    forged_role["authority"]["login_roles"]["joe"] = "carr_writer"
    rejected = run(forged_role)
    check("authority login mapping cannot name routine writer role",
          rejected.returncode != 0 and "authority.login_roles.joe" in rejected.stdout,
          rejected.stdout or rejected.stderr)

    named_device = json.loads(json.dumps(config))
    named_device["device_evidence"]["devices"] = ["invented-device"]
    rejected = run(named_device)
    check("device preflight refuses invented device-name declarations",
          rejected.returncode != 0 and "device_evidence" in rejected.stdout,
          rejected.stdout or rejected.stderr)

    combined_provider_file = json.loads(json.dumps(config))
    combined_provider_file["providers"]["file"] = "~/.config/carr/db.env"
    rejected = run(combined_provider_file)
    check("provider routes must remain in a separate file from database credentials",
          rejected.returncode != 0 and "providers.file" in rejected.stdout,
          rejected.stdout or rejected.stderr)

    broad_routine = json.loads(json.dumps(config))
    broad_routine["routine_jobs"]["credential_env"] = "DATABASE_URL"
    rejected = run(broad_routine)
    check("routine execution refuses a broad database credential declaration",
          rejected.returncode != 0 and "routine_jobs.credential_env" in rejected.stdout,
          rejected.stdout or rejected.stderr)

    overbroad_scope = json.loads(json.dumps(config))
    overbroad_scope["routine_jobs"]["scope"] = "all_deterministic_entrypoints"
    rejected = run(overbroad_scope)
    check("static ledger preflight cannot claim deterministic-entrypoint credential safety",
          rejected.returncode != 0 and "routine_jobs.scope" in rejected.stdout,
          rejected.stdout or rejected.stderr)

    owner_backup = json.loads(json.dumps(config))
    owner_backup["routine_backup"]["login_role"] = "neondb_owner"
    rejected = run(owner_backup)
    check("routine backup declaration cannot name an owner role",
          rejected.returncode != 0 and "routine_backup.login_role" in rejected.stdout,
          rejected.stdout or rejected.stderr)

    broad_backup = json.loads(json.dumps(config))
    broad_backup["routine_backup"]["consumers"].append("bin/nightly.sh")
    rejected = run(broad_backup)
    check("backup capability cannot be declared for every nightly child",
          rejected.returncode != 0 and "routine_backup.consumers" in rejected.stdout,
          rejected.stdout or rejected.stderr)

    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
