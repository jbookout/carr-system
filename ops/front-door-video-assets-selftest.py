#!/usr/bin/env python3
# ci: selftest
"""Hermetic checks for the Drive-free Front Door and video asset boundary."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
env = os.environ.copy()
env["CARR_VAULT"] = "/poison/ambient-drive"


def run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=REPO, env=env, text=True, capture_output=True)


build = REPO / "pipelines/build-front-door.py"
page = REPO / "pipelines/front-door.html"
first = run([sys.executable, str(build)])
assert first.returncode == 0, first.stderr
digest = hashlib.sha256(page.read_bytes()).hexdigest()
second = run([sys.executable, str(build)])
assert second.returncode == 0 and hashlib.sha256(page.read_bytes()).hexdigest() == digest
html = page.read_text()
for forbidden in ("&folder=", "CloudStorage", "GoogleDrive", "/poison/ambient-drive", "DNA%2F", "00_Context%2F"):
    assert forbidden not in html, forbidden
assert html.count("claude://cowork/new?q=") == 32
assert html.count("Use%20the%20CARR%20record-layer%20verbs") == 32
assert "DNA/" not in html and "00_Context/" not in html

listed = run([sys.executable, str(REPO / "video/plan-animated-static.py"), "--list"])
assert listed.returncode == 0 and "concepts:" in listed.stdout

for script in ("build-operatory-math.py", "build-video.py", "make-premiere-cut.py"):
    proc = run([sys.executable, str(REPO / "video" / script)])
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0 and "normal mode refuses Drive assets" in combined, (script, combined)
    assert "/poison/ambient-drive" not in combined

stock = run(["zsh", str(REPO / "video/make-stock-clip.sh")])
assert stock.returncode != 0 and "normal mode refuses Drive assets" in (stock.stdout + stock.stderr)

recovery = run([
    sys.executable, str(REPO / "video/recovery-asset-root.py"),
    "--recovery", "--reason", "asset restore drill", "--vault", "/tmp/recovery-vault",
])
assert recovery.returncode == 0
assert recovery.stdout.strip() == "/tmp/recovery-vault/Marketing/Brand Assets"
assert "NONCANONICAL" in recovery.stderr and "asset restore drill" in recovery.stderr

with tempfile.TemporaryDirectory() as tmp:
    layers = run([sys.executable, str(REPO / "video/make-example-layers.py"), tmp])
    assert layers.returncode == 0, layers.stderr
    assert (Path(tmp) / "08_logo_scale.png").is_file()

inventory = json.loads((REPO / "ops/config/drive-dependencies.v1.json").read_text())
rows = {row["id"]: row for row in inventory["entries"]}
assert rows["normal-front-door-launch-links"]["replacement"]["status"] == "normal_path_repointed_to_record_native_launcher"
assert rows["normal-video-assets"]["replacement"]["status"] == "normal_path_uses_versioned_brand_assets_and_refuses_missing_media_seam"

print("front-door/video asset selftest passed")
