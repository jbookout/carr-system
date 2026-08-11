#!/bin/zsh
# Thin entry point. The tested two-phase implementation lives in Python.
set -eu
REPO="${0:A:h:h}"
VAULT="${CARR_VAULT:-/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI}"
exec "$REPO/.venv/bin/python" "$REPO/tools/doctrine_cutoff.py" \
  --repo "$REPO" --vault "$VAULT" "$@"
