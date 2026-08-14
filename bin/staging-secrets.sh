#!/bin/sh
# staging-secrets.sh — provision the staging Worker's secrets, freshly generated,
# never printed, never copied from production.
#
# WHY FRESH AND NEVER COPIED. If staging reused production's tokens, a leaked
# staging token would open production and the whole isolation exercise would be
# theatre. Gate G1's bar is that staging cannot reach production data OR
# CREDENTIALS; sharing a token fails the second half even when the databases are
# genuinely separate.
#
# WHY NOTHING IS PRINTED. Doctrine: secret values never enter code, chat, logs,
# screenshots, artifacts or model context. This script generates each value,
# pipes it straight into `wrangler secret put`, and writes Joe's own copy to a
# mode-600 file. Nothing reaches stdout but names and outcomes. That is not
# fastidiousness: on 2026-08-13 `neonctl projects create` printed a live
# connection URI with its password into a session transcript, and the only clean
# remedy was destroying the project and rebuilding it. Once a secret is written
# down somewhere it should not be, rotation is the only fix.
#
# WHAT THIS SCRIPT CANNOT DO, and it is not a limitation to work around:
# GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET. Those come from a Google Cloud OAuth
# client, which is created in a browser against Joe's account. Claude never holds
# them — the production wrangler.toml has said so since the beginning. Staging's
# machine-token doors (agent, probe, review, local, capture, ingest) all work
# without them; only interactive Google sign-in on the staging Worker does not.
#
# Usage:
#   bin/staging-secrets.sh            # generate + set everything it can
#   bin/staging-secrets.sh --list     # what is currently set (names only)

set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
WORKER_DIR="$REPO/mcp-server"
WRANGLER="$WORKER_DIR/node_modules/.bin/wrangler"
NEONCTL="$WORKER_DIR/node_modules/.bin/neonctl"
OUT_ENV="$HOME/.config/carr/staging-tokens.env"

[ -x "$WRANGLER" ] || { echo "staging-secrets: wrangler not found at $WRANGLER" >&2; exit 69; }

if [ "${1:-}" = "--list" ]; then
  cd "$WORKER_DIR" && exec "$WRANGLER" secret list --env staging
fi

# A token this script mints. 32 bytes of urandom, hex. Never echoed.
mint() { LC_ALL=C tr -dc 'a-f0-9' < /dev/urandom | head -c 64; }

put() {  # put <SECRET_NAME> <value-on-stdin>
  name="$1"
  if (cd "$WORKER_DIR" && "$WRANGLER" secret put "$name" --env staging >/dev/null 2>&1); then
    echo "  set  $name"
  else
    echo "  FAILED  $name" >&2
    return 1
  fi
}

umask 077
mkdir -p "$(dirname "$OUT_ENV")"
: > "$OUT_ENV"
{
  echo "# carr staging Worker tokens — generated $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "# These open STAGING ONLY. They are not production's and must never be."
  echo "# mode 600, gitignored. Regenerate any time with bin/staging-secrets.sh."
} >> "$OUT_ENV"

echo "staging secrets (values never printed):"

# ── machine-actor token maps ────────────────────────────────────────────────
# Same JSON-map shape the production Worker's index.js checks before the
# OAuthProvider ever sees a request. One key each is enough for staging.
for spec in "AGENT_TOKENS:codex" "PROBE_TOKENS:smoke-probe" \
            "REVIEW_TOKENS:codex-reviewer" "LOCAL_TOKENS:joe-local" \
            "CAPTURE_TOKENS:capture" "INGEST_TOKENS:staging-source"; do
  secret="${spec%%:*}"
  actor="${spec##*:}"
  tok="$(mint)"
  printf '{"%s":"%s"}' "$actor" "$tok" | put "$secret"
  printf 'CARR_STAGING_%s=%s\n' "$(echo "$secret" | sed 's/_TOKENS//')" "$tok" >> "$OUT_ENV"
  unset tok
done

# ── database ────────────────────────────────────────────────────────────────
# Derived from neonctl inside this process, exactly like bin/backup-dump.sh, so
# the DSN never lands on a command line or in a transcript. Staging's project is
# resolved BY NAME so a rebuild does not strand this script on a dead id.
STAGING_ID="$("$NEONCTL" projects list --org-id org-dry-dew-75906281 --output json 2>/dev/null \
  | "$REPO/.venv/bin/python" -c 'import json,sys; d=json.load(sys.stdin); rows=d if isinstance(d,list) else d.get("projects",[]); print(next(r["id"] for r in rows if r.get("name")=="carr-staging"))')"
[ -n "$STAGING_ID" ] || { echo "staging-secrets: could not resolve the carr-staging project" >&2; exit 1; }

STAGING_DSN="$("$NEONCTL" connection-string main --project-id "$STAGING_ID" --role-name neondb_owner 2>/dev/null)"
[ -n "$STAGING_DSN" ] || { echo "staging-secrets: could not derive the staging connection string" >&2; exit 1; }

# BOTH roles point at the same owner for now, and that is a KNOWN GAP rather than
# an oversight. Production separates carr_reader from carr_writer; staging's
# schema arrived through a --no-owner --no-acl dump, so those login roles do not
# exist there yet. Staging is isolated, which is what G1 asks; least-privilege
# WITHIN staging is a smaller, later job. Stated here so nobody reads this file
# as a claim that staging models production's permission model. It does not.
printf '%s' "$STAGING_DSN" | put "DATABASE_URL_READER"
printf '%s' "$STAGING_DSN" | put "DATABASE_URL_WRITER"
unset STAGING_DSN

chmod 600 "$OUT_ENV"
echo ""
echo "Joe's copy of the staging tokens: $OUT_ENV (mode 600, not in git)"
echo "NOT set, and not mine to set: GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET."
echo "  Staging's machine doors work without them; Google sign-in on staging does not."
