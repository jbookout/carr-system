#!/usr/bin/env bash
# Download into the restored archive; unpack only after a complete fetch.
set -euo pipefail
if zsh --version >/dev/null 2>&1; then
  exit 0
fi
mkdir -p "$ZSH_ARCHIVE_DIR/partial"
# APT trusts a cached archive's size. Verify its bytes against the runner's
# authenticated package indexes before letting APT reuse it.
python3 - "$ZSH_ARCHIVE_DIR" <<'PY'
import hashlib
from pathlib import Path
import re
import signal
import subprocess
import sys
from urllib.parse import unquote

signal.alarm(30)
root = Path(sys.argv[1])
for archive in root.glob("*.deb"):
    match = re.fullmatch(r"([a-z0-9][a-z0-9+.-]*)_([^_]+)_([a-z0-9][a-z0-9-]*)\.deb", archive.name)
    verified = False
    if match and archive.is_file() and not archive.is_symlink():
        package, version, architecture = match.groups()
        metadata = subprocess.run(["apt-cache", "show", "--no-all-versions", package + ":" + architecture],
                                  capture_output=True, text=True, timeout=5)
        if metadata.returncode not in (0, 100):
            metadata.check_returncode()
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        for paragraph in metadata.stdout.split("\n\n"):
            fields = dict(line.split(": ", 1) for line in paragraph.splitlines() if ": " in line)
            if (fields.get("Package") == package and fields.get("Version") == unquote(version)
                    and fields.get("Architecture") == architecture and fields.get("SHA256") == digest):
                verified = True
                break
    if not verified:
        quarantine = root / "quarantine"
        quarantine.mkdir(exist_ok=True)
        archive.rename(quarantine / archive.name)
        print("Ignoring unverified zsh archive: " + archive.name, file=sys.stderr)
PY
apt_options=(-o "Dir::Cache::archives=$ZSH_ARCHIVE_DIR" -o APT::Keep-Downloaded-Packages=true -o Acquire::Retries=2 -o Acquire::http::Timeout=15 -o Acquire::https::Timeout=15 -o DPkg::Lock::Timeout=30)
fetched=0
for attempt in 1 2 3; do
  if timeout --kill-after=5s 60s sudo apt-get "${apt_options[@]}" install -y -qq --no-install-recommends --download-only zsh; then
    fetched=1
    break
  fi
  echo "zsh download attempt ${attempt} failed" >&2
  if [ "${attempt}" -eq 1 ]; then
    timeout --kill-after=5s 60s sudo apt-get "${apt_options[@]}" update -qq \
      || echo "index refresh failed; retrying the download" >&2
  fi
  if [ "${attempt}" -lt 3 ]; then
    sleep "$((attempt * ZSH_RETRY_BACKOFF))"
  fi
done
if [ "${fetched}" -ne 1 ]; then
  echo "zsh could not be downloaded after 3 attempts" >&2
  exit 1
fi
timeout --kill-after=5s 120s sudo apt-get "${apt_options[@]}" install -y -qq --no-install-recommends --no-download zsh
