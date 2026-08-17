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

# ── database ownership boundary ────────────────────────────────────────────
# This token rotator NEVER derives, reads, or overwrites database credentials.
# tools/provision-staging-app-writer.py owns the two independently persisted,
# SQL-created least-authority login credentials and installs each Worker secret
# only after an authenticated authority postflight. Keeping the paths separate
# means rotating machine tokens cannot silently widen either database door back
# to neondb_owner.

chmod 600 "$OUT_ENV"
echo ""
echo "Joe's copy of the staging tokens: $OUT_ENV (mode 600, not in git)"
echo "NOT set, and not mine to set: GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET."
echo "  Staging's machine doors work without them; Google sign-in on staging does not."
