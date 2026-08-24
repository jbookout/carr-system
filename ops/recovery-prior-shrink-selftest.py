#!/usr/bin/env python3
"""Hermetic adversarial contract for typed recovery-prior verb shrink."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WRAPPER = ROOT / "bin" / "deploy-worker.sh"
source = WRAPPER.read_text(encoding="utf-8")

# No caller-controlled escape survives argument parsing: reject before the
# wrapper reaches its real dependency, network, or provider checks.
unknown = subprocess.run(["sh", str(WRAPPER), "--allow-shrink"],
                         cwd=ROOT, capture_output=True, text=True)
assert unknown.returncode == 64, unknown.stderr
assert "unknown argument '--allow-shrink'" in unknown.stderr

# The only lower-count route is syntactically closed around a typed `prior`
# prepare.  That writer is the existing DB function that refuses a mismatched
# candidate/prior/SHA/service or a prior without a complete Production readback.
gate = 'if [ "$RECOVERY_STEP" != "prior" ] || [ "$TARGET_ENV" != "staging" ]; then'
prepare = '"$PY" "$REPO/tools/ops-record.py" staging-attempt prepare'
assert gate in source and prepare in source
assert source.index(gate) < source.index(prepare)
assert '--prior-release-key "$RECOVERY_PRIOR_RELEASE_KEY"' in source
assert '--recovery-attempt-id "$RECOVERY_ATTEMPT_ID" --recovery-step prior' in source
assert '--git-sha "$HEAD_SHA" --correlation "$RECOVERY_ATTEMPT_ID"' in source
assert '"$RECOVERY_STEP" = "restore_only"' in source

# The provider command remains after the gate; a generic source/standalone or
# mismatched prior never reaches Wrangler with a lower registry count.
assert source.index(prepare) < source.index('"$WRANGLER" deploy --env "$TARGET_ENV"')
print("recovery-prior-shrink: standalone/manual/mismatched routes remain closed")
