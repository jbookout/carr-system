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
# NOT read-only any more, and deliberately so (ORDER 18 addendum, 2026-07-31).
# Every verb here is write:false EXCEPT the last check, a log-activity call with
# a FIXED idempotency key: it inserted once, on the first run in history, and
# replays for ever after. That one row is the price of covering the write path,
# and a twelve-hour production outage is what not covering it cost.

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

# check <label> <verb> <args> [grep-pattern] [second-grep-pattern]
# Passes when every rep is non-error, not isError, and (if given) matches the
# pattern(s). A verb returning an empty-but-valid result still passes: this is a
# plumbing check, not a data assertion.
check() {
  local label="$1" verb="$2" args="$3" pattern="${4:-}" pattern2="${5:-}" i why=""
  for i in $(seq 1 "$REPS"); do
    call "$verb" "$args"
    if echo "$RESULT" | grep -q '"error"'; then why="transport/protocol error"
    elif echo "$RESULT" | grep -q '"isError":true'; then why="verb returned isError"
    elif [ -n "$pattern" ] && ! echo "$RESULT" | grep -q "$pattern"; then
      why="expected /$pattern/ in the result"
    elif [ -n "$pattern2" ] && ! echo "$RESULT" | grep -q "$pattern2"; then
      why="expected /$pattern2/ in the result"
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

# --- ORDER 18: the intro graph is reachable under the READER role ---------------
# 'Jon Shaw' is a real vendor (V-BNK-013) who introduced C-155 Dr. James Allen
# Tyrer. The name 'Tyrer' cannot appear in the parties block (that block matches
# the query name) nor in the deals block (no deal is named Jon Shaw), so a
# response to query 'Jon Shaw' that contains Tyrer can only have come from the
# connections block reading v_party_graph. The chain is the probe.
echo
# (the result arrives as a JSON string inside the MCP envelope, so the keys are
#  backslash-escaped on the wire — match them that way, not as bare quotes)
check "graph probe: find surfaces the Shaw -> Tyrer intro" \
      find '{"query":"Jon Shaw"}' '\\"connections\\"' 'Tyrer'

# --- ORDER 18 addendum: the WRITE path, using the A1 replay property -----------
# WHY THIS EXISTS. On 2026-07-31 every ref-based WRITE verb returned 500
# "permission denied for view v_ref_index" for roughly twelve hours. ORDER 7
# moved resolveSubject onto that view and granted it to carr_reader only, but
# resolveSubject also runs inside the write transaction under carr_writer. This
# script was all-green throughout, because it only ever exercised reads.
#
# The probe is safe to run forever: the idempotency key below is FIXED and the
# arguments never change, so the A1 envelope inserts on the first run in history
# (2026-07-31) and REPLAYS on every run after — same response, no second row.
# Anything that grows here would be an envelope bug, which is itself worth
# catching. kind is 'note' on purpose: is_contact=false since 0017, so the probe
# cannot move a Last Touch value in the exports.
echo
_w_ok=1; _w_why=""
for i in $(seq 1 "$REPS"); do
  call log-activity '{"idempotency_key":"smoke-write-probe-permanent","ref":"V-CPA-006","kind":"note","summary":"smoke write probe — replayed, never duplicated"}'
  if echo "$RESULT" | grep -q '"error"'; then _w_ok=0; _w_why="transport/protocol error"; break; fi
  if echo "$RESULT" | grep -q '"isError":true'; then _w_ok=0; _w_why="verb returned isError (resolveSubject under carr_writer?)"; break; fi
  if ! echo "$RESULT" | grep -q '\\"ok\\":true'; then _w_ok=0; _w_why="no ok:true in the envelope response"; break; fi
  # rep 1 may legitimately be the first-ever insert; every rep after it must replay
  if [ "$i" -gt 1 ] && ! echo "$RESULT" | grep -q '\\"replayed\\":true'; then
    _w_ok=0; _w_why="rep $i did NOT replay — the envelope wrote twice"; break
  fi
  [ "$i" -lt "$REPS" ] && sleep "$REP_SLEEP"
done
if [ "$_w_ok" -eq 1 ]; then
  echo "  ok    write path: log-activity resolves + replays  (${REPS}/${REPS})"; pass=$((pass+1))
else
  echo "  FAIL  write path — $_w_why"
  echo "        $(echo "$RESULT" | head -c 220)"; fail=$((fail+1))
fi

echo
echo "passed $pass · failed $fail"
[ "$fail" -eq 0 ] || exit 1
