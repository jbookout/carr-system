#!/usr/bin/env bash
# smoke-reads.sh — exercise EVERY read verb against production, under the reader role.
#
# Why this exists: the build session proved "23 verbs end-to-end" and two of them
# had never worked. `find` and `catch-me-up` queried base tables that carr_reader
# cannot see, and the gap survived from build day until ORDER 6's done-test
# happened to trip over it (amendment 11). A proof only covers what it touched —
# so the coverage is a script now, not a lesson someone has to remember.
#
# RUN THIS AFTER EVERY WORKER DEPLOY.
#   ./mcp-server/smoke-reads.sh
# Exit 0 = all read verbs healthy. Non-zero = at least one check failed.
#
# Read-only by construction: every verb below has write:false in the registry.

set -uo pipefail

API="${CARR_MCP_URL:-https://api.practicecre.com/mcp}"
ENVFILE="${CARR_MCP_ENV:-$HOME/.config/carr/mcp-tokens.env}"
[ -f "$ENVFILE" ] && { set -a; . "$ENVFILE"; set +a; }
TOKEN="${CARR_MCP_TOKEN_JOE:-${JOE_TOKEN:-}}"
if [ -z "$TOKEN" ]; then echo "FAIL: no MCP token (looked in $ENVFILE)"; exit 2; fi

pass=0; fail=0
_id=0

# call <verb> <json-args> -> prints the result text, sets RESULT
call() {
  _id=$((_id+1))
  RESULT=$(curl -s --max-time 30 -X POST "$API" \
    -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
    -d "{\"jsonrpc\":\"2.0\",\"id\":$_id,\"method\":\"tools/call\",\"params\":{\"name\":\"$1\",\"arguments\":$2}}")
}

# Each check runs REPS times and every rep must pass.
#
# One sample is not enough, learned the hard way on 2026-07-31: immediately after
# a deploy this script reported all-green while `find` was still failing 3 times
# in 5. Cloudflare had the new version at 100%, but warm isolates were still
# serving the old bundle for about a minute. A single-shot probe cannot tell
# "fixed" from "half-deployed", and reporting green on a half-deployed Worker is
# exactly the false pass this script exists to prevent.
REPS="${SMOKE_REPS:-3}"
REP_SLEEP="${SMOKE_REP_SLEEP:-3}"

# check <label> <verb> <args> [grep-pattern]
# Passes when every rep is non-error, not isError, and (if given) matches the
# pattern. A verb returning an empty-but-valid result still passes: this is a
# plumbing check, not a data assertion.
check() {
  local label="$1" verb="$2" args="$3" pattern="${4:-}" i why=""
  for i in $(seq 1 "$REPS"); do
    call "$verb" "$args"
    if echo "$RESULT" | grep -q '"error"'; then why="transport/protocol error"
    elif echo "$RESULT" | grep -q '"isError":true'; then why="verb returned isError"
    elif [ -n "$pattern" ] && ! echo "$RESULT" | grep -q "$pattern"; then
      why="expected /$pattern/ in the result"
    else why=""; fi
    if [ -n "$why" ]; then
      echo "  FAIL  $label — $why (rep $i of $REPS)"
      echo "        $(echo "$RESULT" | head -c 220)"; fail=$((fail+1)); return
    fi
    [ "$i" -lt "$REPS" ] && sleep "$REP_SLEEP"
  done
  echo "  ok    $label  (${REPS}/${REPS})"; pass=$((pass+1))
}

echo "read-verb smoke test -> $API"
echo

# --- the seven read verbs, all must be non-error -------------------------------
check "find (real name: Hughes)"      find             '{"query":"Hughes"}'   'Hughes'
check "catch-me-up (real record C-112)" catch-me-up    '{"ref":"C-112","limit":5}' '"'
check "today-triage"                  today-triage     '{}'
check "deal-board"                    deal-board       '{}'
check "lead-hot"                      lead-hot         '{}'
check "stale-records"                 stale-records    '{}'
check "integrity-digest"              integrity-digest '{}'

# --- resolver behaviour (amendment 7): ambiguity must refuse to guess ----------
echo
_amb_ok=1
for i in $(seq 1 "$REPS"); do
  call catch-me-up '{"ref":"dental"}'
  echo "$RESULT" | grep -q 'needs_disambiguation' || { _amb_ok=0; break; }
  [ "$i" -lt "$REPS" ] && sleep "$REP_SLEEP"
done
if [ "$_amb_ok" -eq 1 ]; then
  echo "  ok    ambiguous name returns needs_disambiguation  (${REPS}/${REPS})"; pass=$((pass+1))
else
  echo "  FAIL  ambiguous name did NOT return needs_disambiguation"
  echo "        $(echo "$RESULT" | head -c 220)"; fail=$((fail+1))
fi

echo
echo "passed $pass · failed $fail"
[ "$fail" -eq 0 ] || exit 1
