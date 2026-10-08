#!/usr/bin/env bash
# Download into the restored archive; unpack only after a complete fetch.
set -euo pipefail
if zsh --version >/dev/null 2>&1; then
  exit 0
fi
mkdir -p "$ZSH_ARCHIVE_DIR/partial"
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
