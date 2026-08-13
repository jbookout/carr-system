#!/bin/zsh
# set-local-actor.sh — record which human this Mac belongs to, for local-verb.mjs.
#
# WHY THIS EXISTS (Phase 1, 2026-08-13, council-ordered): mcp-server/local-verb.mjs
# used to take the actor as a caller-typed argv slug DEFAULTING TO "joe" — any
# local caller could claim any identity, and every historical receipt written
# through it is unverifiable because of it. This script is the one-time (per
# machine) act that fixes the identity local-verb reads instead: it writes
# ~/.config/carr/local-actor.json, and local-verb.mjs now refuses to run without
# that file — no argv slug, no default.
#
# THE HONEST LIMIT, stated once here and again in local-verb.mjs: this closes
# ACCIDENTAL and DEFAULT impersonation (a stray argv, a copy-pasted "joe" that
# should have been "dell"). It does NOT stop a deliberate local attacker — any
# process running as the same Mac user can read or overwrite this file same as
# this script can. That boundary is the server side's job (the actor table,
# the token-issuing OAuth flow in identity.js), not this file's. This script
# only makes the ordinary, unintentional case impossible.
#
# USAGE:
#   bin/set-local-actor.sh <slug>            # refuses if the file already exists
#   bin/set-local-actor.sh <slug> --force    # overwrites an existing file
#
# Deliberately dumb and safe (per the design): it cannot validate <slug> against
# the real actor table offline, so it does not try. It only soft-warns if the
# slug is outside the two partners it knows about below — it does not block,
# because the real gate is server-side and already exists (local-verb.mjs's
# write path already refuses an unknown actor: "no actor <slug>").
set -u

# Known partner slugs, for the soft warning only — NOT an enforced allowlist.
KNOWN_SLUGS=(joe dell)

SLUG="${1:-}"
FORCE=0
for arg in "$@"; do
  [ "$arg" = "--force" ] && FORCE=1
done

if [ -z "$SLUG" ] || [ "$SLUG" = "--force" ]; then
  echo "usage: bin/set-local-actor.sh <slug> [--force]" >&2
  exit 2
fi

FOUND=0
for k in "${KNOWN_SLUGS[@]}"; do
  [ "$k" = "$SLUG" ] && FOUND=1
done
if [ "$FOUND" -eq 0 ]; then
  echo "warning: '$SLUG' is not one of the known partner slugs (${KNOWN_SLUGS[*]})." >&2
  echo "         continuing anyway — local-verb.mjs's write path will refuse it if the" >&2
  echo "         actor table does not recognize it. Reads are not checked against the" >&2
  echo "         actor table at all, so a typo here would go quietly unnoticed on reads." >&2
fi

CONFIG_DIR="$HOME/.config/carr"
IDENTITY_FILE="$CONFIG_DIR/local-actor.json"

if [ -f "$IDENTITY_FILE" ] && [ "$FORCE" -eq 0 ]; then
  echo "already set: $IDENTITY_FILE exists (pass --force to overwrite)" >&2
  cat "$IDENTITY_FILE" >&2
  exit 1
fi

mkdir -p "$CONFIG_DIR"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Write, then lock down permissions BEFORE anything else can read it.
umask 077
cat > "$IDENTITY_FILE" <<EOF
{
  "actor_slug": "$SLUG",
  "established_at": "$NOW",
  "established_by": "set-local-actor"
}
EOF
chmod 600 "$IDENTITY_FILE"

echo "wrote $IDENTITY_FILE"
echo "  actor_slug: $SLUG"
echo "  established_at: $NOW"
ls -l "$IDENTITY_FILE"
