#!/usr/bin/env python3
# ci: selftest
"""Hermetic checks for the Drive-free Front Door and video asset boundary."""
from __future__ import annotations

import hashlib
import html as html_lib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
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

tile_prompts: dict[str, str] = {}
for href, label in re.findall(
    r'<a class="tile-main"[^>]+href="([^"]+)".*?<span class="tile-label">(.*?)</span>',
    html,
):
    parsed = urllib.parse.urlparse(html_lib.unescape(href).replace("claude://", "https://"))
    tile_prompts[html_lib.unescape(label)] = urllib.parse.parse_qs(parsed.query)["q"][0]
assert "new-lead or new-client" in tile_prompts["New Prospect"]
assert "allocate the next" not in tile_prompts["New Prospect"] and "live sheet" not in tile_prompts["New Vendor"]
assert "comp-ingress seam as unavailable" in tile_prompts["File a Comp"]
assert "prepare-conversation" in tile_prompts["Prep for a Meeting"]
assert all(verb in tile_prompts["Open the Dashboard"] for verb in ("today-triage", "deal-room-board", "loop-board"))
for misleading in ("write the row", "write a sheet", "confirm the filename", "save it to DNA", "fall back to"):
    assert all(misleading not in prompt.lower() for prompt in tile_prompts.values()), misleading

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
    layer_dir = Path(tmp) / "layers"
    layers = run([sys.executable, str(REPO / "video/make-example-layers.py"), str(layer_dir)])
    assert layers.returncode == 0, layers.stderr
    assert (layer_dir / "08_logo_scale.png").is_file()

    log = Path(tmp) / "choreography.tsv"
    job = Path(tmp) / "job.json"
    plan = Path(tmp) / "plan.env"
    recovery_controls = ["--recovery", "--reason", "asset restore drill", "--vault", str(Path(tmp) / "vault")]
    initial = run([
        sys.executable, str(REPO / "video/plan-animated-static.py"), str(layer_dir), str(log),
        "--name", "contract-proof", "--json-out", str(job), "--plan-out", str(plan),
        *recovery_controls,
    ])
    assert initial.returncode == 0 and job.is_file() and plan.is_file(), initial.stderr
    values = dict(re.findall(r'^(\w+)="(.*)"$', plan.read_text(), re.MULTILINE))
    commit = run([
        sys.executable, str(REPO / "video/plan-animated-static.py"), str(layer_dir), str(log),
        "--name", "contract-proof", "--concept", values["CONCEPT"], "--sfx", values["SFX_KEY"],
        "--commit", *recovery_controls,
    ])
    assert commit.returncode == 0 and "contract-proof" in log.read_text(), commit.stderr
    assert "NONCANONICAL" in initial.stderr and "NONCANONICAL" in commit.stderr

wrapper = (REPO / "video/make-animated-static.sh").read_text()
assert '"${RECOVERY_ARGS[@]}"' in wrapper
assert "choreography commit failed; removed uncommitted outputs" in wrapper
commit_boundary = wrapper.index("if ! python3 \"$PLANNER\"")
assert 'echo "OK:' not in wrapper[:commit_boundary]

inventory = json.loads((REPO / "ops/config/drive-dependencies.v1.json").read_text())
rows = {row["id"]: row for row in inventory["entries"]}
assert rows["normal-front-door-launch-links"]["replacement"]["status"] == "normal_path_repointed_to_record_native_launcher"
assert rows["normal-video-assets"]["replacement"]["status"] == "normal_path_uses_versioned_brand_assets_and_refuses_missing_media_seam"

print("front-door/video asset selftest passed")
