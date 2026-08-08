#!/usr/bin/env bash
# Thin wrapper around the Blotato publishing API (https://help.blotato.com/api).
# Source this file, then call the functions below. Requires BLOTATO_API_KEY in the environment.
set -euo pipefail

# Non-interactive/non-login shells don't auto-source ~/.zprofile, so load it explicitly.
[ -f "$HOME/.zprofile" ] && source "$HOME/.zprofile"

: "${BLOTATO_API_KEY:?Set BLOTATO_API_KEY in ~/.zprofile first (never hardcode it here or in chat)}"

BLOTATO_API_BASE="https://backend.blotato.com/v2"

# Upload media from a publicly-accessible URL (e.g. a Canva export link).
# Prints the Blotato-hosted media URL to use in a post's mediaUrls array.
blotato_upload_media_url() {
  local source_url="$1"
  curl -sS -X POST "$BLOTATO_API_BASE/media" \
    -H "Content-Type: application/json" \
    -H "blotato-api-key: $BLOTATO_API_KEY" \
    -d "$(jq -n --arg url "$source_url" '{url: $url}')" \
  | jq -r '.url'
}

# Create a post from a JSON request body file (see SKILL.md for the per-platform shape).
# Prints the postSubmissionId.
blotato_create_post() {
  local body_file="$1"
  curl -sS -X POST "$BLOTATO_API_BASE/posts" \
    -H "Content-Type: application/json" \
    -H "blotato-api-key: $BLOTATO_API_KEY" \
    -d @"$body_file" \
  | jq -r '.postSubmissionId'
}

# Poll a submission until it reaches a terminal state. Prints the final status JSON
# (status: published|failed, publicUrl when published, errorMessage when failed).
blotato_wait_for_publish() {
  local submission_id="$1"
  local max_tries=60
  local resp status
  for _ in $(seq 1 "$max_tries"); do
    resp=$(curl -sS "$BLOTATO_API_BASE/posts/$submission_id" -H "blotato-api-key: $BLOTATO_API_KEY")
    status=$(echo "$resp" | jq -r '.status')
    if [[ "$status" == "published" || "$status" == "failed" ]]; then
      echo "$resp"
      return 0
    fi
    sleep 2
  done
  echo "$resp"
  return 1
}
