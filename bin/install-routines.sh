#!/bin/bash
set -euo pipefail
routine_repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec "$routine_repo/.venv/bin/python" "$routine_repo/tools/routines/install.py" "$@"
