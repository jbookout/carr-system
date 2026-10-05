#!/usr/bin/env bash
set -eu
umask 077

fail() {
  printf '%s\n' "$1" >&2
  exit 1
}

trap 'fail "key intake failed"' ERR
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
key_name="$(python3 - "$script_dir/../ops/config/github-app.json" 2>/dev/null <<'PY'
import json
import pathlib
import re
import sys

try:
    name = json.loads(pathlib.Path(sys.argv[1]).read_text())["key_file"]
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or name in {".", ".."}:
        raise ValueError
    print(name)
except (OSError, ValueError, KeyError, TypeError):
    sys.exit(1)
PY
)"
config_dir="$HOME/.config/carr"
destination="$config_dir/$key_name"
mkdir -p "$config_dir" 2>/dev/null

if [ -L "$destination" ]; then
  fail "key intake failed: installed key is a symbolic link"
fi
if [ -f "$destination" ]; then
  chmod 600 "$destination" 2>/dev/null
  printf '%s\n' installed
  exit 0
fi
if [ -e "$destination" ]; then
  fail "key intake failed: installation destination is not a file"
fi

downloads="$HOME/Downloads"
newest=""
for candidate in "$downloads"/carr-watchdog-jbookout*.private-key.pem; do
  [ -f "$candidate" ] && [ ! -L "$candidate" ] || continue
  if [ -z "$newest" ] || [ "$candidate" -nt "$newest" ]; then
    newest="$candidate"
  fi
done
[ -n "$newest" ] || fail "key intake failed: no matching downloaded key"
chmod 600 "$newest" 2>/dev/null
mv "$newest" "$destination" 2>/dev/null
chmod 600 "$destination" 2>/dev/null
printf '%s\n' installed
