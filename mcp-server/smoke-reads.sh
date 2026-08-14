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
# Every verb here is write:false EXCEPT the last two checks, which use FIXED
# idempotency keys: they wrote once, on the first run in history, and replay for
# ever after. Those few rows are the price of covering the write path, and a
# twelve-hour production outage is what not covering it cost.
# FORTY-FOUR checks as of the 2026-08-02 0066 marketing pass: seventeen plumbing
# checks (ORDER 36's analysis write path was the seventeenth), SEVENTEEN
# negative-answer probes, and TEN 0066 marketing probes at the very bottom.
# Twenty-three of them sit behind SIX capability gates (org visibility, the
# merge-split response shape, the unwalkable-edge report, the 0063 contract,
# 0066's two-stage worker/migration gate, and — added 2026-08-14 — the auto-edge
# path's 'probe' profile gate) and print SKIP rather than FAIL when the Worker,
# the schema or the credential's profile predates or excludes the thing they
# cover, so a healthy run is anywhere from 21 to 44 — the script says which gate
# is closed and why.
# The count had been stale at "eleven as of ORDER 19" since ORDER 27 — ORDERS 27,
# 33, 34 and 36 each added a check without moving it. Recount when you add one.
#
# MEASURED 2026-08-14 under CARR_MCP_PROBE_TOKEN against production:
# passed 33 · failed 0, with three SKIP blocks (auto-edge, record-counter 0063,
# 0066 marketing) — all three profile-locked rather than broken. That is the
# baseline a scheduled run is expected to reproduce; a drop below it is real.
#
# THE 0066 SECTION IS ALL REFUSALS, and that is not a shortcut — it is the only
# safe shape for those verbs (a success probe would mint a permanent fake
# campaign or hang a fake number on a real post) and it is also the behaviour
# that matters: the marketing verbs exist to say NO to the shapes that turn an
# unmeasured post into a measured zero. Their idempotency keys are NOT frozen
# fixtures, because a refusal rolls back and stores no tool_call row.
#
# ══════════════════════════════════════════════════════════════════════════════
# RE-CREDITED (loop #192, 2026-08-06), replacing the PREFLIGHT block below in
# place. The 2026-08-05 version of that block correctly reported this suite
# dead in the water: the legacy PARTNER_TOKENS bearer it authenticated with was
# retired 2026-08-03 (#111b), and nothing OAuth-shaped can replace it here —
# this is a cron-friendly script running curl, not an interactive session that
# can complete a Google sign-in.
#
# THE FIX IS A NEW, NARROWER CREDENTIAL, NOT A REBUILT OLD ONE. `PROBE_TOKENS`
# (mcp-server/src/index.js) is a bearer, checked before the OAuthProvider ever
# sees the request, that maps to ONE actor ('smoke-probe') pinned server-side to
# a 'probe' capability profile (mcp-server/src/mcp.js) — reads, plus EXACTLY the
# three write verbs this file replays under a frozen idempotency key
# (log-activity, set-next-action, complete-action). ?profile= cannot widen it;
# every other write verb refuses with not_in_profile. It is not a second copy of
# the retired bearer: that one authenticated as a full human actor on the full
# profile, and this one cannot.
#
# PROVISIONING (JOE ONLY — an agent is blocked from production writes and from
# ever holding a secret value):
#   1. Generate the token:
#        openssl rand -hex 32
#   2. Put it in the Worker as PROBE_TOKENS, a JSON map keyed by actor slug
#      (same shape as INGEST_TOKENS) — REPLACES the whole map, so if a second
#      probe identity is ever added, put both keys together:
#        cd ~/carr-system/mcp-server
#        wrangler secret put PROBE_TOKENS
#        # paste: {"smoke-probe":"<the token from step 1>"}
#   3. Insert the 'smoke-probe' actor row (kind='automation') — prepared, NOT
#      run, as pipelines/provision-smoke-probe.sql. Apply it through db-tap
#      (never a raw psql command substitution — see that tool's own docstring):
#        cd ~/carr-system && .venv/bin/python tools/db-tap.py sql pipelines/provision-smoke-probe.sql
#      Without this row, every one of the three probe write verbs refuses with
#      actor_not_provisioned even though the token authenticates fine.
#   4. Add the SAME token from step 1 to this suite's env file:
#        # ~/.config/carr/mcp-tokens.env (600, outside the repo)
#        CARR_MCP_PROBE_TOKEN=<the token from step 1>
#   5. Deploy the Worker (classifier-gated; Joe/the parent session runs this,
#      never this script) and re-run smoke-reads.sh. The preflight below picks
#      CARR_MCP_PROBE_TOKEN up automatically and needs no other change here.
# ══════════════════════════════════════════════════════════════════════════════

set -uo pipefail

API="${CARR_MCP_URL:-https://api.practicecre.com/mcp}"
ENVFILE="${CARR_MCP_ENV:-$HOME/.config/carr/mcp-tokens.env}"
[ -f "$ENVFILE" ] && { set -a; . "$ENVFILE"; set +a; }

# PREFLIGHT (2026-08-06, loop #192 re-credit): CARR_MCP_PROBE_TOKEN is the
# preferred credential — see the provisioning runbook just above. When it is
# absent this falls back to the old partner-token env vars purely so a run
# against a Worker that somehow still accepted one would not silently do
# nothing; on production today that fallback always lands on the RETIRED AUTH
# message below, exactly as it did before this suite was re-credited.
PROBE_MODE=0
TOKEN="${CARR_MCP_PROBE_TOKEN:-}"
if [ -n "$TOKEN" ]; then
  PROBE_MODE=1
else
  TOKEN="${CARR_MCP_TOKEN_JOE:-${JOE_TOKEN:-}}"
fi
if [ -z "$TOKEN" ]; then
  echo "FAIL: no MCP token (looked for CARR_MCP_PROBE_TOKEN, then CARR_MCP_TOKEN_JOE / JOE_TOKEN, in $ENVFILE)"
  exit 2
fi

# Prove the token is ACCEPTED before running 44 checks, under whichever role it
# authenticates as. A rejected token would otherwise print dozens of phantom
# FAILs that look exactly like a broken deploy.
_pf=$(curl -sS "$API" -X POST \
    -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
    -d '{"jsonrpc":"2.0","id":0,"method":"tools/list"}' 2>/dev/null)
if printf '%s' "$_pf" | grep -q '"invalid_token"'; then
  if [ "$PROBE_MODE" -eq 1 ]; then
    echo "PROBE TOKEN REJECTED — CARR_MCP_PROBE_TOKEN did not authenticate against $API."
    echo "This is NOT the retired-PARTNER_TOKENS case (that path is gone on purpose and"
    echo "this one should work). Most likely causes, in order: (1) the value here does not"
    echo "match the Worker's PROBE_TOKENS secret — re-check step 2 of the provisioning"
    echo "runbook above; (2) the Worker has not been deployed with the probe-token check in"
    echo "mcp-server/src/index.js yet. Note this failure is auth, not provisioning — even a"
    echo "correctly-authenticating token still needs the 'smoke-probe' actor row (step 3,"
    echo "pipelines/provision-smoke-probe.sql) before any write check below will pass."
    exit 3
  fi
  echo "RETIRED AUTH — this token is no longer accepted by the Worker (PARTNER_TOKENS"
  echo "retired 2026-08-03, #111b). The 44 checks below would all print phantom FAILs."
  echo "Provision CARR_MCP_PROBE_TOKEN per the runbook above this preflight block to run"
  echo "this suite for real, under the locked 'probe' profile. Until then, post-deploy"
  echo "verification is ~/carr-system/.venv/bin/python ops/nightly-verb-probe.py (gate +"
  echo "all views) plus one live verb through a partner's OAuth connector."
  exit 3
fi
if [ "$PROBE_MODE" -eq 1 ]; then
  echo "probe token accepted — running under the locked 'probe' profile: reads, plus"
  echo "log-activity / set-next-action / complete-action only. Checks needing any other"
  echo "write verb are expected to print SKIP (profile: probe), not FAIL — that is the"
  echo "server-side lock working, not a regression."
  echo
fi

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

# --- ORDER 32: the multi-hop half, and the probe is a REAL two-hop chain --------
# V-ATT-009 Dion Moniz's Links names Jon Shaw; V-BNK-013 Jon Shaw's Links names
# C-155 Dr. James Allen Tyrer. Neither row names the other end, so a response to
# target C-155 containing 'Dion Moniz' can only have come from the recursive walk
# joining two separate edges. If the traversal ever breaks, this goes red.
check "who-do-we-know: the two-hop Moniz -> Shaw -> Tyrer path" \
      who-do-we-know '{"target":"C-155"}' 'Dion Moniz' '\\"hops\\":2'
# The refuse-to-guess half. 'Ric' matches more than one graph node (V-BNK-030 Ric
# McClanahan and V-BNK-034 Ric Nickelsen), and the verb must hand back candidates
# rather than pick one (amendment 7). needs_disambiguation travels as isError by
# the ToolError convention, so `check` cannot express this — same bespoke loop the
# catch-me-up ambiguity probe above uses, and for the same reason.
_wamb_ok=1
for i in $(seq 1 "$REPS"); do
  call who-do-we-know '{"target":"Ric"}'
  echo "$RESULT" | grep -q 'needs_disambiguation' || { _wamb_ok=0; break; }
  echo "$RESULT" | grep -q 'V-BNK-034' || { _wamb_ok=0; break; }
  [ "$i" -lt "$REPS" ] && sleep "$REP_SLEEP"
done
if [ "$_wamb_ok" -eq 1 ]; then
  echo "  ok    who-do-we-know: ambiguous name returns candidates, never a guess  (${REPS}/${REPS})"; pass=$((pass+1))
else
  echo "  FAIL  who-do-we-know did NOT refuse an ambiguous name with candidates"
  echo "        $(echo "$RESULT" | head -c 220)"; fail=$((fail+1))
fi

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

# --- ORDER 19: the completion path, the same fixed-key replay pattern ----------
# WHY THIS EXISTS. Until 2026-07-31 NOTHING in this system could mark a ball
# done: production held 212 open, 1 dropped and zero done, so the on_complete
# half of the cadence engine had no input path at all. `complete-action` is that
# path, and a verb the whole follow-up machinery depends on deserves a tripwire
# rather than a memory.
#
# SAFE TO RUN FOR EVER, for the same reason the write probe is: both keys below
# are FIXED and the arguments never change, so the pair inserted exactly once in
# history and replays on every run after. The subject is deliberate too —
# 'AMA Law Office' is a CLOSED/LOST deal that carried zero next_action rows, and
# no seeded cadence rule fires on a deal subject, so completing this fixture
# spawns nothing and displaces no real ball. If a deal-lane on_complete rule is
# ever seeded, move the fixture rather than deleting this probe.
#
# THE TWO ARGUMENT STRINGS BELOW ARE FROZEN. The envelope hashes the arguments
# with the key, so editing a single character of either JSON body makes every
# future run return `key_reuse` instead of a replay, and the probe fails for
# ever after on a typo. Change the key too, or leave them alone.
echo
_c_ok=1; _c_why=""
for i in $(seq 1 "$REPS"); do
  call set-next-action '{"idempotency_key":"smoke-ball-probe-permanent","ref":"AMA Law Office","description":"smoke probe fixture — permanent, replayed, never a real ball"}'
  if ! echo "$RESULT" | grep -q '\\"ok\\":true'; then _c_ok=0; _c_why="the probe fixture ball could not be set"; break; fi
  call complete-action '{"idempotency_key":"smoke-complete-probe-permanent","ref":"AMA Law Office","outcome":"smoke probe — completed once, replayed for ever after"}'
  if echo "$RESULT" | grep -q '"error"'; then _c_ok=0; _c_why="transport/protocol error"; break; fi
  if echo "$RESULT" | grep -q '"isError":true'; then _c_ok=0; _c_why="verb returned isError (deployed? resolveSubject under carr_writer?)"; break; fi
  if ! echo "$RESULT" | grep -q '\\"ok\\":true'; then _c_ok=0; _c_why="no ok:true in the envelope response"; break; fi
  # rep 1 may legitimately be the first-ever completion; every rep after replays
  if [ "$i" -gt 1 ] && ! echo "$RESULT" | grep -q '\\"replayed\\":true'; then
    _c_ok=0; _c_why="rep $i did NOT replay — the envelope completed twice"; break
  fi
  [ "$i" -lt "$REPS" ] && sleep "$REP_SLEEP"
done
if [ "$_c_ok" -eq 1 ]; then
  echo "  ok    completion path: complete-action marks done + replays  (${REPS}/${REPS})"; pass=$((pass+1))
else
  echo "  FAIL  completion path — $_c_why"
  echo "        $(echo "$RESULT" | head -c 220)"; fail=$((fail+1))
fi

# --- ORDER 27 EXT / ORDER 33: the counterparty + attribution views are live ----
# counterparty-history on a name nobody carries must answer with the honest
# "not captured" note rather than error — proves the verb + view + reader grant.
echo
check "counterparty-history: unknown name gets the honest no-capture answer" \
      counterparty-history '{"target":"Nobody Smokeprobe Xyzzy"}' 'No counterparty history'

# source-attribution: the funnel view answers, and the reconciliation row that
# keeps the money math honest ('unattributed') is present.
check "source-attribution: lanes answer incl. the unattributed reconciler" \
      source-attribution '{}' '\\"lanes\\"' 'unattributed'

# --- ORDER 34: links[] on log-activity — the auto-edge capture path ------------
# FROZEN probe (fixed key + args, same rule as the two probes above: edit
# nothing, ever). The edge V-BNK-013 -> C-155 'intro' already exists in
# production (ORDER 32's proven chain), so this exercises ref resolution +
# kind validation + the conflict path (existing:true) without ever growing the
# graph. Rep 1 may insert the one fixture activity row; every later rep replays.
#
# PROFILE-GATED 2026-08-14 (Program 3 triage). This was the SIXTH gate and it was
# missing: the locked 'probe' profile (mcp-server/src/mcp.js) admits log-activity
# but NOT log-activity carrying links[], which the server refuses as
# not_in_profile — correctly, since an edge write is exactly what the narrow
# profile exists to withhold. The check had no gate, so it printed a hard FAIL on
# every probe-token run and read as a broken auto-edge path when nothing was
# broken at all. It is a SKIP under 'probe' for the same reason the other five
# gates are: the suite must not report a deliberate server-side lock as a
# regression. Under a partner's OAuth session PROBE_MODE is 0 and it runs for real.
echo
if [ "$PROBE_MODE" -eq 1 ]; then
  echo "  SKIP  auto-edge path — log-activity links[] is outside the locked 'probe' profile"
  echo "        (server returns not_in_profile). Not a failure — that is the lock working."
  echo "        Run under a partner's OAuth session to exercise the auto-edge path."
else
_l_ok=1; _l_why=""
for i in $(seq 1 "$REPS"); do
  call log-activity '{"idempotency_key":"smoke-links-probe-permanent","ref":"V-BNK-013","kind":"note","summary":"smoke links probe — edge already exists, replayed for ever after","links":[{"from_ref":"V-BNK-013","to_ref":"C-155","kind":"intro"}]}'
  if echo "$RESULT" | grep -q '"error"'; then _l_ok=0; _l_why="transport/protocol error"; break; fi
  if echo "$RESULT" | grep -q '"isError":true'; then _l_ok=0; _l_why="verb returned isError (ref resolution under carr_writer?)"; break; fi
  if ! echo "$RESULT" | grep -q '\\"ok\\":true'; then _l_ok=0; _l_why="no ok:true in the envelope response"; break; fi
  if ! echo "$RESULT" | grep -q '\\"existing\\":true'; then _l_ok=0; _l_why="links[] did not return the existing edge — resolution or upsert broke"; break; fi
  if [ "$i" -gt 1 ] && ! echo "$RESULT" | grep -q '\\"replayed\\":true'; then
    _l_ok=0; _l_why="rep $i did NOT replay — the envelope wrote twice"; break
  fi
  [ "$i" -lt "$REPS" ] && sleep "$REP_SLEEP"
done
if [ "$_l_ok" -eq 1 ]; then
  echo "  ok    auto-edge path: log-activity links[] resolves refs + returns existing edge  (${REPS}/${REPS})"; pass=$((pass+1))
else
  echo "  FAIL  auto-edge path — $_l_why"
  echo "        $(echo "$RESULT" | head -c 220)"; fail=$((fail+1))
fi
fi

# --- ORDER 36: kind:analysis — the dossier write path --------------------------
# FROZEN probe (fixed key + args; edit nothing, ever — the envelope hashes the
# arguments with the key, so a one-character change returns key_reuse for ever).
#
# WHY THIS EXISTS. ORDER 36 makes the 20 prospect dossiers generated renders, and
# the ONLY way new analysis reaches one is log-activity kind:analysis. That slug
# has to clear TWO independent gates that fail in different places: the enum in
# this Worker's inputSchema (a deploy) and the activity_kind ref-table row (a
# migration, 0028). Deploy the Worker without applying 0028 and the row dies on
# the FK; apply 0028 without deploying and the verb rejects the argument. This
# probe is red until BOTH have landed, which is exactly the joint check the
# supervisor's deploy-plus-apply needs.
#
# THE SUBJECT IS A VENDOR, DELIBERATELY. v_export_dossier_analysis only joins
# client/lead subjects carrying notes_path, so an analysis row on V-CPA-006 can
# never surface in a rendered dossier. A client fixture would print "smoke probe"
# as the newest analysis, in full, in a real prospect file. kind:analysis is also
# is_contact=false in 0028, so like the note probe above it cannot move a Last
# Touch value in the exports.
echo
_a_ok=1; _a_why=""
for i in $(seq 1 "$REPS"); do
  call log-activity '{"idempotency_key":"smoke-analysis-probe-permanent","ref":"V-CPA-006","kind":"analysis","summary":"smoke analysis probe — replayed, never duplicated","detail":"ORDER 36 probe: proves the analysis slug clears both the Worker enum and the activity_kind ref table."}'
  if echo "$RESULT" | grep -q '"error"'; then _a_ok=0; _a_why="transport/protocol error"; break; fi
  if echo "$RESULT" | grep -q '"isError":true'; then
    _a_ok=0; _a_why="verb returned isError — Worker not deployed with the analysis slug, or 0028 not applied (FK on activity_kind)"; break; fi
  if ! echo "$RESULT" | grep -q '\\"ok\\":true'; then _a_ok=0; _a_why="no ok:true in the envelope response"; break; fi
  # rep 1 may legitimately be the first-ever insert; every rep after it replays
  if [ "$i" -gt 1 ] && ! echo "$RESULT" | grep -q '\\"replayed\\":true'; then
    _a_ok=0; _a_why="rep $i did NOT replay — the envelope wrote twice"; break
  fi
  [ "$i" -lt "$REPS" ] && sleep "$REP_SLEEP"
done
if [ "$_a_ok" -eq 1 ]; then
  echo "  ok    analysis path: log-activity kind:analysis accepted + replays  (${REPS}/${REPS})"; pass=$((pass+1))
else
  echo "  FAIL  analysis path — $_a_why"
  echo "        $(echo "$RESULT" | head -c 220)"; fail=$((fail+1))
fi

# ══════════════════════════════════════════════════════════════════════════════
# NEGATIVE-ANSWER PROBES (2026-08-02 cold-session audit)
#
# EVERYTHING ABOVE THIS LINE IS A PLUMBING CHECK. `check` says so in its own
# comment: "A verb returning an empty-but-valid result still passes: this is a
# plumbing check, not a data assertion." That is a reasonable contract and it is
# also exactly how a real bug survived roughly forty migrations.
#
# THE BUG. Ask `find` for "Henry Schein" and it answered "Henry Pruett" — a
# trigram hit on one word of the query — while 17 real Henry Schein party rows
# sat in the table untouched. `who-do-we-know "Henry Schein"` went further and
# replied "No record and no graph node matches that name", which is not a miss,
# it is a false statement about the book. Root cause was in 0016 and inherited
# unchanged all the way to 0056: every branch of v_ref_index reached party
# THROUGH a role, so the view indexed ROLES, not subjects, and 432 of 1,084
# parties — including all 415 orgs — were invisible to the primary lookup verb.
#
# THIS SCRIPT WAS GREEN EVERY ONE OF THOSE DAYS, and correctly so by its own
# rules: the verb responded, it was not an error, the envelope was well formed.
# A verb that confidently returns the WRONG answer is indistinguishable from a
# healthy one to a plumbing check, and it is far more dangerous than one that
# errors: an error gets investigated, "we don't know them" gets believed and
# acted on.
#
# SO THIS CLASS ASSERTS THE ANSWER, NOT THE RESPONSE. Every probe below fails on
# a wrong answer, not merely on a dead verb. They come in matched pairs on
# purpose — a probe that says "never claim absence" can be passed by a verb that
# has simply lost the ability to say it, so each such probe is paired with one
# that requires the same claim to still appear when it is TRUE.
#
# FIXTURE DURABILITY. Nothing here depends on a count that grows, a date, a
# stage, or a row anybody edits in the normal course of work:
#   · 'Henry Schein' — the reported defect itself, a supplier org nobody owns.
#   · 'Mia Arafa' C-036/C-046 — a completed merge. A merge is permanent and the
#     tombstone is kept on purpose (the 0016 posture, so a search for a merged
#     name learns where it went), so this pair cannot rot back.
#   · 'Qwertzuiop Vraxmandel' — deliberate nonsense that will never be a record.
# If a fixture ever does rot, MOVE THE FIXTURE. Do not delete the probe.
# ══════════════════════════════════════════════════════════════════════════════

# refute <label> <verb> <args> <pattern-that-must-NOT-appear> [pattern-that-MUST-appear]
# The mirror of `check`. Same REPS discipline, same half-deployed-Worker
# reasoning: one clean sample proves nothing right after a deploy.
refute() {
  local label="$1" verb="$2" args="$3" forbidden="$4" required="${5:-}" i why=""
  for i in $(seq 1 "$REPS"); do
    call "$verb" "$args"
    if echo "$RESULT" | grep -q '"error"'; then why="transport/protocol error"
    elif echo "$RESULT" | grep -q '"isError":true'; then why="verb returned isError"
    elif echo "$RESULT" | grep -q "$forbidden"; then
      why="WRONG ANSWER: the result contains /$forbidden/, which it must not"
    elif [ -n "$required" ] && ! echo "$RESULT" | grep -q "$required"; then
      why="expected /$required/ in the result"
    else why=""; fi
    if [ -n "$why" ]; then
      echo "  FAIL  $label — $why (rep $i of $REPS)"
      echo "        $(echo "$RESULT" | head -c 220)"; fail=$((fail+1)); return
    fi
    [ "$i" -lt "$REPS" ] && sleep "$REP_SLEEP"
  done
  echo "  ok    $label  (${REPS}/${REPS})"; pass=$((pass+1))
}

echo
echo "--- negative-answer probes: a WRONG answer must fail, not just a dead verb ---"

# THE ORG-VISIBILITY GATE. Two independent things have to have landed for an org
# to be findable: migration 0056 (the party branch of v_ref_index — the data
# half) and a Worker carrying the widened subject_type filter (0056's own header
# says so: both read verbs hardcoded subject_type in ('lead','client','vendor'),
# "the new rows are queryable but not yet queried").
#
# WHEN NEITHER HAS LANDED THIS IS A SKIP, NOT A FAILURE, and the discriminator
# is structural rather than a guess: the `organizations` key either exists in the
# response shape or it does not. No key at all means the deploy predates the fix
# — nothing has regressed, so going red would be a false alarm on work that is
# simply not done yet. Key present but WRONG CONTENT is a real regression and
# goes red. CAP_ORG carries that decision to the second probe so the two cannot
# disagree about whether the feature exists.
CAP_ORG=1
call find '{"query":"Henry Schein"}'
if ! echo "$RESULT" | grep -q '\\"organizations\\"'; then
  CAP_ORG=0
  echo "  SKIP  org visibility — this Worker has no \"organizations\" block in its find response."
  echo "        That means migration 0056 (v_ref_index party branch) and/or the widened"
  echo "        subject_type filter in mcp-server/src/tools.js has not reached production."
  echo "        Apply 0056 and deploy the Worker, then these two probes go live. Not a failure."
fi

if [ "$CAP_ORG" -eq 1 ]; then
  # The reported defect, asserted directly. 'Henry Schein' must come back, and
  # the organizations block must not be empty — an empty block with a trigram
  # hit on 'Henry' in parties is precisely the original wrong answer wearing the
  # new response shape. The 17-row count is deliberately NOT asserted: the right
  # fix for 17 duplicate supplier rows is to merge them, and a probe that goes
  # red when someone cleans up the data is a probe that teaches people to delete
  # probes.
  refute "find 'Henry Schein' surfaces the ORG, not a trigram hit on 'Henry'" \
         find '{"query":"Henry Schein"}' '\\"organizations\\":\[\]' 'Henry Schein'

  # The false-absence half, and the worse of the two symptoms. This verb used to
  # answer "No record and no graph node matches that name" for an org carrying
  # 17 live rows. That sentence must never appear for a name the book holds.
  refute "who-do-we-know 'Henry Schein' does NOT claim absence" \
         who-do-we-know '{"target":"Henry Schein"}' 'No record and no graph node matches' \
         '\\"matching_records\\"'
fi

# THE PAIR TO THE PROBE ABOVE. "Never say you don't know them" is trivially
# satisfied by a verb that has lost the ability to say it at all, and that
# failure would look identical to a fix. So the absence claim is required to
# still fire on a name that genuinely is not in the book. Together the two
# probes assert the sentence is load-bearing rather than dead code.
check "who-do-we-know DOES claim absence for a name nobody carries" \
      who-do-we-know '{"target":"Qwertzuiop Vraxmandel"}' 'No record and no graph node matches'

# THE SAME SHAPE ON `find`: a query with no answer must return NO answer. The
# original defect was not that find missed Henry Schein, it was that find
# offered an unrelated human in its place and the caller had no way to tell.
# Empty is the correct answer to an unanswerable query, and asserting it is the
# only thing that stops a near-miss from being dressed up as a hit.
check "find returns EMPTY for a name nobody carries (no trigram near-miss)" \
      find '{"query":"Qwertzuiop Vraxmandel"}' '\\"parties\\":\[\]' '\\"organizations\\":\[\]'

# MERGED RECORDS SURFACE AS TOMBSTONES RATHER THAN VANISHING. C-036 and C-046
# are both 'Mia Arafa'; C-046 is the completed merge. Both must come back, and
# the merged flag must be present — a merge that removes the old ref from `find`
# silently breaks every note, email and document that still cites C-046, and it
# does it in the same "the record simply isn't there" way the Henry Schein bug
# did. Asserting BOTH refs is the point: the survivor alone would pass a lookup
# while the pointer that makes the merge navigable had been lost.
check "merged record C-046 surfaces as a tombstone, not a disappearance" \
      find '{"query":"Mia Arafa"}' 'C-046' '\\"merged\\":true'
check "…and the surviving record C-036 comes back with it" \
      find '{"query":"Mia Arafa"}' 'C-036'

# ══════════════════════════════════════════════════════════════════════════════
# LOOP #132 — A TOMBSTONE IS NOT A DUPLICATE, AND IT IS NEVER A TARGET
#
# THE BUG THE PROBES ABOVE COULD NOT CATCH, and it is worth being precise about
# why, because the answer is the whole design of this section. The Henry Schein
# probe above deliberately does NOT assert the row count: "the right fix for 17
# duplicate supplier rows is to merge them, and a probe that goes red when
# someone cleans up the data is a probe that teaches people to delete probes."
# That reasoning was right. Then 0059 DID merge them — 415 org rows into 306
# survivors and 109 tombstones — and `find` went on reporting
# `duplicate_rows: 17` for a company that now has exactly ONE live row, because
# the grouping query (b0fda91) landed before 0059 and was never taught about
# merged_into. The same for Musicologie: 13 reported, 1 live, 12 retired.
# `who-do-we-know "Musicologie"` was worse — it offered five tombstones as
# selectable records and never mentioned the survivor at all.
#
# So the probes below assert counts after all, and the difference is that these
# counts are INVARIANTS rather than inventory. `party_org_identity_uniq` (0059)
# permits exactly one live row per organisation name, so `live_rows: 1` is
# enforced by the database and a 2 is a real defect. Tombstone counts cannot
# grow either: a new tombstone requires a new duplicate, which that same index
# forbids. The one thing that moves them is 0059's documented reversal, which is
# a loud deliberate act — if it happens, MOVE THE FIXTURE, do not delete the probe.
#
# THE MERGE-SPLIT GATE. Same structural discriminator as CAP_ORG, one layer in:
# the response either carries the separated `live_rows` key or the old blended
# `duplicate_rows`. Blended means the Worker predates this fix — nothing has
# regressed, so SKIP. Neither key means the org block itself is missing, which
# CAP_ORG has already reported.
CAP_SPLIT=0
if [ "$CAP_ORG" -eq 1 ]; then
  CAP_SPLIT=1
  call find '{"query":"Henry Schein"}'
  if echo "$RESULT" | grep -q '\\"duplicate_rows\\"'; then
    CAP_SPLIT=0
    echo
    echo "  SKIP  merge split — this Worker still reports the blended \"duplicate_rows\" count."
    echo "        Deploy mcp-server (loop #132) and these probes go live. Not a failure."
  fi
fi

if [ "$CAP_SPLIT" -eq 1 ]; then
  echo
  # The reported defect, asserted as a number. The blended key must be GONE — not
  # merely accompanied by better ones, because a caller reading the old key would
  # still be reading the wrong answer.
  refute "find 'Henry Schein': one LIVE org row, and no blended duplicate count" \
         find '{"query":"Henry Schein"}' '\\"duplicate_rows\\"' '\\"live_rows\\":1'
  # …and the tombstones are still counted rather than deleted from the answer.
  # Paired with the zero-side probe below: together they prove retired_aliases is
  # read from the data and is not a constant in either direction.
  # SCOPED 2026-08-14 (Program 3 triage). This forbade the string
  # \"retired_aliases\":0 ANYWHERE in the payload, which was only ever correct
  # while "Henry Schein" matched exactly one org row. A second live org row,
  # "Henry Schein, Inc." (live_rows:1, refs:[null], no tombstones), now matches
  # the same query and legitimately carries retired_aliases:0 — so the suite
  # failed on a TRUE answer. The forbidden pattern is anchored to the Henry
  # Schein object itself: [^}]* cannot cross the object boundary, and the
  # trailing \", after Schein excludes "Henry Schein, Inc." (whose name has a
  # bare comma there, not an escaped quote). The required side asserts a real
  # P- tombstone is listed rather than a specific count, because the count grows
  # with every merge and a hardcoded 16 would just be tomorrow's false failure.
  refute "…and its retired aliases are counted, not dropped" \
         find '{"query":"Henry Schein"}' \
         '\\"name\\":\\"Henry Schein\\",[^}]*\\"retired_aliases\\":0' '\\"retired_refs\\":\[\\"P-'
  check  "…and retired_aliases really reads 0 for an org that has no tombstones" \
         find '{"query":"1st Med Transitions"}' '\\"retired_aliases\\":0' '1st Med Transitions'

  # Musicologie: 13 party rows, 12 retired, and the survivor NO LONGER HAS A PARTY
  # REF. This fixture was rewritten 2026-08-02 and the reason is the whole lesson.
  # 0061 gave org party P-0111 a client record, and v_ref_index indexes SUBJECTS
  # rather than roles (0056), so P-0111 stopped appearing as a party and started
  # appearing as client C-161. The verb still filtered its org branch to
  # subject_type='party', saw only tombstones, reported live_rows:0, and emitted a
  # note swearing the survivor "carries a DIFFERENT name and is not in this result"
  # while the survivor sat in the SAME payload under the SAME name. The old fixture
  # asserted refs:["P-0111"], which is now the WRONG answer — asserting it would
  # have pinned the verb to a shape the database had already left behind. A fixture
  # that outlives its data is how a probe starts defending a defect.
  refute "find 'Musicologie': survivor promoted to a role ref, not a lost merge" \
         find '{"query":"Musicologie"}' '\\"all_retired\\":true' '\\"role_refs\\":\[\\"C-161\\"\]'
  # The pair: the tombstones must still be COUNTED, not quietly dropped, or the fix
  # above could pass by simply forgetting they exist.
  check  "…and its 12 tombstones are still counted, not dropped" \
         find '{"query":"Musicologie"}' '\\"retired_aliases\\":12'

  # who-do-we-know handed P-0840, P-1044, P-0909 and P-0796 back as candidates and
  # never named the survivor. A caller that links or writes to one of those defeats
  # the merge, so a tombstone ref must not appear in this verb's answer AT ALL —
  # find is where tombstones stay navigable, with their refs; this verb resolves.
  # It names C-161 now for the same reason as above: that is where the survivor is.
  refute "who-do-we-know 'Musicologie' names the survivor and never a tombstone" \
         who-do-we-know '{"target":"Musicologie"}' 'P-0840' 'C-161'
  refute "who-do-we-know 'Henry Schein' names the survivor and never a tombstone" \
         who-do-we-know '{"target":"Henry Schein"}' 'P-0099' 'P-0055'
  # Refusing to OFFER a tombstone must not mean pretending it is not there.
  check  "…and it still says how many retired aliases it declined to offer" \
         who-do-we-know '{"target":"Musicologie"}' '\\"retired_alias_count\\":12'
  # The pair, same shape as the absence-claim pair above: a count that is always
  # nonzero would pass the probe above while telling the caller nothing.
  check  "…and that count reads 0 for a name nobody carries" \
         who-do-we-know '{"target":"Qwertzuiop Vraxmandel"}' '\\"retired_alias_count\\":0'
fi

# ══════════════════════════════════════════════════════════════════════════════
# LOOP #133 — AN EDGE THAT CANNOT BE WALKED MUST BE REPORTED, NOT DROPPED
#
# v_party_graph holds 31 edges; seven have a NULL ref on one endpoint and six of
# those seven are Joe's own `can_introduce` edges, because Joe is party P-1084
# with no client/lead/vendor row and therefore no business ref. WHO_EDGES filters
# NULL endpoints out of the walk, correctly — the walker keys on refs — but the
# verb then answered as though the relationship did not exist. Asking who reaches
# Heather Lavallo returned Chris Kelly and said nothing about Joe, whose offered
# introduction is sitting in the record.
#
# THE FIXTURE IS CHOSEN TO SURVIVE THE REAL FIX. specs/party-graph-ref-fallback.md
# proposes teaching v_party_graph to fall back to the P- ref; when that lands,
# Joe becomes a walkable node and this edge moves out of `unwalkable_edges` and
# into `paths`. The probe asserts 'Joe Bookout' appears in the answer to "who
# gets me to V-CPA-036" — true through the unwalkable report today, true through
# the path after the view change, and false in both the old code and any future
# regression that silently drops the edge again.
CAP_UNWALK=1
call who-do-we-know '{"target":"V-CPA-036"}'
if ! echo "$RESULT" | grep -q '\\"unwalkable_edges\\"'; then
  CAP_UNWALK=0
  echo
  echo "  SKIP  unwalkable-edge report — this Worker has no \"unwalkable_edges\" key."
  echo "        Deploy mcp-server (loop #133) and these two probes go live. Not a failure."
fi

if [ "$CAP_UNWALK" -eq 1 ]; then
  echo
  check "who-do-we-know V-CPA-036 surfaces Joe's own offered intro, one way or the other" \
        who-do-we-know '{"target":"V-CPA-036"}' 'Joe Bookout' '\\"unwalkable_edges\\"'
  # The pair. A report that always lists something is not a report, so the zero
  # side needs a node that is genuinely in the graph, genuinely has a path, and
  # genuinely has nothing blocked.
  #
  # FIXTURE MOVED C-155 -> V-BNK-013, 2026-08-14 (Program 3 triage). C-155 stopped
  # being a valid zero-side the moment the ternary Joe->Tyrer 'introduced' edge
  # was recorded (the shape migration 0051 defined, from = us): Joe is party
  # P-1084 with no business ref, so that edge has from_ref null and lands in
  # C-155's unwalkable_edges. The verb REPORTING it is loop #133 working exactly
  # as designed — the suite was failing on correct behaviour, and the fixture's
  # premise ("C-155 has nothing blocked"), not the system, is what expired.
  # V-BNK-013 (Jon Shaw) is in_graph with one walkable path and an empty list.
  # If this check ever fails, read it as "someone recorded a ref-less edge into
  # Shaw" and re-verify the premise before touching the verb.
  check "…and the list is EMPTY where nothing is blocked, not a hardcoded fixture" \
        who-do-we-know '{"target":"V-BNK-013"}' '\\"unwalkable_edges\\":\[\]'
fi

# ══════════════════════════════════════════════════════════════════════════════
# 0063 — record-counter CAN CARRY A CLAIM AND A SUBMARKET
#
# WHY THIS ONE PROBES THE SCHEMA AND NOT THE WRITE PATH, deliberately and not out
# of laziness. Every other write probe here is safe because its idempotency key
# is frozen: it inserted once in history and replays for ever. record-counter has
# no such fixture — a probe call would insert a REAL negotiation_round on a REAL
# deal, and worse, a Worker that has NOT been deployed with these arguments would
# ignore `claims` as an unknown property and insert the round anyway. That is the
# one shape of probe that does damage when the thing it tests is missing. So the
# assertion is the verb's published contract, read from tools/list, which cannot
# write anything and still fails on a Worker that lacks the parameters.
#
# Rehearse the actual write on a Neon branch instead, never against production:
#   DATABASE_URL=<branch> node mcp-server/local-verb.mjs record-counter '{...}'
#
# tools/list is a different JSON-RPC method, so it needs its own call. Note the
# payload here is RAW json — unlike a tools/call result, which arrives as a JSON
# string inside the envelope — so these patterns carry no backslash escaping.
echo
list_call() {
  _id=$((_id+1))
  RESULT=$(curl -s --max-time 30 -X POST "$API" \
    -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
    -d "{\"jsonrpc\":\"2.0\",\"id\":$_id,\"method\":\"tools/list\",\"params\":{}}")
}

# Same gate discipline as CAP_ORG: absent entirely means the Worker predates the
# work, which is not a regression. Present-but-wrong is a regression and goes red.
CAP_0063=1
list_call
if echo "$RESULT" | grep -q '"error"'; then
  CAP_0063=0
  echo "  FAIL  tools/list errored — cannot read the published verb contract"
  echo "        $(echo "$RESULT" | head -c 220)"; fail=$((fail+1))
elif ! echo "$RESULT" | grep -q 'submarket_condition'; then
  CAP_0063=0
  if [ "$PROBE_MODE" -eq 1 ]; then
    echo "  SKIP  record-counter 0063 contract — record-counter is a write verb outside the"
    echo "        locked 'probe' profile (see mcp-server/src/mcp.js), so tools/list under this"
    echo "        token never publishes its schema at all. Not a failure — run under a partner's"
    echo "        OAuth session to exercise this contract."
  else
    echo "  SKIP  record-counter 0063 contract — this Worker publishes no submarket_condition"
    echo "        parameter, so the counterparty-observation verb support is not deployed yet."
    echo "        Deploy mcp-server and this probe goes live. Not a failure."
  fi
fi

if [ "$CAP_0063" -eq 1 ]; then
  _rc_ok=1; _rc_why=""
  for i in $(seq 1 "$REPS"); do
    list_call
    if echo "$RESULT" | grep -q '"error"'; then _rc_ok=0; _rc_why="tools/list errored"; break; fi
    if ! echo "$RESULT" | grep -q 'negotiation_claim_type slug'; then
      _rc_ok=0; _rc_why="submarket_condition ships but claims[] does not — half the 0063 contract"; break; fi
    # THE PAIRED ASSERTION, and it is the one that matters most. 0063 marks
    # `deadline` derived=true and makes it physically unloggable, because the
    # deadline already IS negotiation_round.expires_on. A schema that offered a
    # deadline claim would advertise a write the database refuses, and the only
    # thing keeping a future editor from adding one is this sentence.
    if ! echo "$RESULT" | grep -q 'THE DEADLINE LIVES HERE'; then
      _rc_ok=0; _rc_why="expires_on no longer states that it is the ONLY home for a deadline"; break; fi
    [ "$i" -lt "$REPS" ] && sleep "$REP_SLEEP"
  done
  if [ "$_rc_ok" -eq 1 ]; then
    echo "  ok    record-counter publishes submarket_condition + claims[], deadline still expires_on  (${REPS}/${REPS})"; pass=$((pass+1))
  else
    echo "  FAIL  record-counter 0063 contract — $_rc_why"
    echo "        $(echo "$RESULT" | head -c 220)"; fail=$((fail+1))
  fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# 0066 — THE MARKETING LANE: A CAMPAIGN CAN BE RECORDED, AND SILENCE IS NOT ZERO
#
# WHY EVERY PROBE HERE IS A REFUSAL, and why that is stronger coverage than a
# success would be. The frozen-key trick that makes the log-activity and
# complete-action probes safe does not transfer to these verbs: open-campaign
# would mint a REAL campaign row, and campaign_name_uniq means that fixture would
# then exist for ever in the one table whose entire value is that every row in it
# means something. measure-placement would put a permanent fake number on a real
# post. Neither is a price worth paying.
#
# A refusal writes nothing by construction — the ToolError is thrown inside the
# transaction, which rolls back — so these probes are free to run for ever AND
# their idempotency keys are never stored, which is why (unlike the frozen write
# probes above) the argument strings here may be edited without causing key_reuse.
#
# And a refusal is the behaviour that actually matters in this lane. The whole
# point of 0066 is that the verbs say NO to the specific shapes that turn silence
# into a number: an all-zero payload, an empty call, a verdict over unmeasured
# placements, a hand-written row wearing the scheduled pull's provenance. A probe
# that only proved the happy path would pass on a Worker that had lost every one
# of those guards.
#
# TWO GATES, because two things deploy separately and either can be behind.
#   CAP_MKT   — the Worker publishes the verbs at all (tools/list).
#   CAP_0066  — migration 0066 has been applied (the verbs answer
#               migration_not_applied until it is). This gate is not a
#               formality: on 2026-08-02 the deployed Worker still predated
#               commits 49a53ba and dc83da6, so record-finding, reassign-deal and
#               retire-rule were in tools.js and NOT callable. These four verbs
#               ship into exactly that state and stay there until Joe deploys.

# refuses <label> <verb> <args> <required-error-code> [forbidden-string]
# Passes when the verb returns isError with the NAMED error code. A verb that
# succeeds, returns a different refusal, or errors at the transport fails.
refuses() {
  local label="$1" verb="$2" args="$3" code="$4" forbidden="${5:-}" i why=""
  for i in $(seq 1 "$REPS"); do
    call "$verb" "$args"
    if echo "$RESULT" | grep -q '"error"'; then why="transport/protocol error"
    elif ! echo "$RESULT" | grep -q '"isError":true'; then
      why="the verb ACCEPTED input it must refuse"
    elif ! echo "$RESULT" | grep -q "$code"; then
      why="refused with the wrong reason — expected /$code/"
    elif [ -n "$forbidden" ] && echo "$RESULT" | grep -q "$forbidden"; then
      why="the refusal contains /$forbidden/, which it must not"
    else why=""; fi
    if [ -n "$why" ]; then
      echo "  FAIL  $label — $why (rep $i of $REPS)"
      echo "        $(echo "$RESULT" | head -c 220)"; fail=$((fail+1)); return
    fi
    [ "$i" -lt "$REPS" ] && sleep "$REP_SLEEP"
  done
  echo "  ok    $label  (${REPS}/${REPS})"; pass=$((pass+1))
}

echo
CAP_MKT=1
list_call
if echo "$RESULT" | grep -q '"error"'; then
  CAP_MKT=0
  echo "  FAIL  tools/list errored — cannot read the marketing verb contract"
  echo "        $(echo "$RESULT" | head -c 220)"; fail=$((fail+1))
elif ! echo "$RESULT" | grep -q '"open-campaign"'; then
  CAP_MKT=0
  if [ "$PROBE_MODE" -eq 1 ]; then
    echo "  SKIP  0066 marketing verbs — open-campaign, score-campaign, attach-to-campaign and"
    echo "        measure-placement are write verbs outside the locked 'probe' profile (see"
    echo "        mcp-server/src/mcp.js). Their idempotency keys vary per call by design (a"
    echo "        refusal stores no row), so a probe call against them would be a LIVE write"
    echo "        attempt rather than a replay — exactly what this profile exists to rule out."
    echo "        Not a failure — run under a partner's OAuth session to exercise this lane."
  else
    echo "  SKIP  0066 marketing verbs — this Worker publishes no open-campaign."
    echo "        open-campaign, score-campaign, attach-to-campaign and measure-placement are"
    echo "        in mcp-server/src/tools.js and are NOT deployed. Until the Worker ships them"
    echo "        the marketing lane still cannot record a campaign. Deploy, then re-run."
    echo "        Not a failure."
  fi
fi

if [ "$CAP_MKT" -eq 1 ]; then
  echo
  # ── contract probes: tools/list cannot write, and still catches present-but-wrong
  # The criterion is REQUIRED at open time. A campaign whose success test can be
  # written after the numbers arrive is not a campaign, it is a press release, and
  # the only thing keeping that field mandatory is this assertion and the schema.
  _c_ok=1; _c_why=""
  for i in $(seq 1 "$REPS"); do
    list_call
    if echo "$RESULT" | grep -q '"error"'; then _c_ok=0; _c_why="tools/list errored"; break; fi
    if ! echo "$RESULT" | grep -q '"success_criterion","starts_on","channels"'; then
      _c_ok=0; _c_why="open-campaign no longer REQUIRES success_criterion/starts_on/channels"; break; fi
    # The doctrine sentence, same shape as 0063's "THE DEADLINE LIVES HERE". If a
    # future edit softens this, the model stops being told the one rule the verb
    # exists to enforce, and the probe is what notices.
    if ! echo "$RESULT" | grep -q 'must never read as a zero'; then
      _c_ok=0; _c_why="measure-placement no longer states that an unmeasured placement is not a zero"; break; fi
    # Blocker 2's contract: a finding can be about a THING.
    if ! echo "$RESULT" | grep -q '"auto","party","campaign","platform","pillar","format"'; then
      _c_ok=0; _c_why="record-finding does not publish the four non-party subject kinds — a marketing finding still has no home"; break; fi
    # The no-second-birth-path decision, kept stated. All 89 pieces are born in
    # pull_placement_metrics.py keyed on external_id, so a creation path here
    # would mint duplicates on the ingest's next run. If this sentence goes, the
    # reasoning has been lost and the duplicate path is one edit away.
    if ! echo "$RESULT" | grep -q 'IT DOES NOT CREATE CONTENT'; then
      _c_ok=0; _c_why="attach-to-campaign no longer states that it binds content rather than creating it"; break; fi
    [ "$i" -lt "$REPS" ] && sleep "$REP_SLEEP"
  done
  if [ "$_c_ok" -eq 1 ]; then
    echo "  ok    0066 contract: criterion required, zero-rule stated, findings reach non-party subjects  (${REPS}/${REPS})"; pass=$((pass+1))
  else
    echo "  FAIL  0066 contract — $_c_why"
    echo "        $(echo "$RESULT" | head -c 220)"; fail=$((fail+1))
  fi

  # ── the migration gate. Every marketing verb calls require0066 first, so this
  # single call tells us whether the schema half has landed.
  CAP_0066=1
  call measure-placement '{"idempotency_key":"smoke-0066-gate","placement":"smoke-nonexistent","source":"blotato_api"}'
  if echo "$RESULT" | grep -q 'migration_not_applied'; then
    CAP_0066=0
    echo
    echo "  SKIP  0066 behaviour probes — the Worker ships the verbs but migration 0066 is NOT"
    echo "        applied, so every marketing verb correctly answers migration_not_applied and"
    echo "        writes nothing. Run: ~/carr-system/run.sh migrate --apply --yes"
    echo "        (This SKIP is itself the answer to \"is the lane live?\" — it is not.)"
  fi

  if [ "$CAP_0066" -eq 1 ]; then
    echo
    # THE PROVENANCE REFUSAL, and the assertion is also about ORDER. The source
    # check must fire BEFORE the placement lookup, or a caller would learn their
    # post id is wrong and never learn that the source string was a lie. The
    # forbidden pattern is what proves the ordering.
    refuses "measure-placement refuses to forge the scheduled pull's provenance" \
            measure-placement \
            '{"idempotency_key":"smoke-0066-src","placement":"smoke-nonexistent","source":"blotato_api"}' \
            'reserved_source' 'placement_not_found'

    # THE CENTRAL RAIL. An empty measurement call must not be quietly accepted:
    # accepting it would leave the placement looking exactly like the 73 nobody
    # has measured, which is the failure this verb was built to prevent.
    refuses "measure-placement refuses an EMPTY measurement (silence is not a result)" \
            measure-placement \
            '{"idempotency_key":"smoke-0066-empty","placement":"smoke-nonexistent","source":"platform_native"}' \
            'nothing_to_record'

    # …and its opposite: "no data" and "here is the data" are two different acts
    # and may not be sent as one.
    refuses "measure-placement refuses metrics and unavailable in the same act" \
            measure-placement \
            '{"idempotency_key":"smoke-0066-both","placement":"smoke-nonexistent","source":"platform_native","unavailable":true,"metrics":{"views_count":5}}' \
            'ambiguous_measurement'

    # An unavailability with no reason is indistinguishable from apathy six months
    # later, and the three possible reasons lead to three different next moves.
    refuses "measure-placement refuses 'no data' with no reason" \
            measure-placement \
            '{"idempotency_key":"smoke-0066-noreason","placement":"smoke-nonexistent","source":"platform_native","unavailable":true}' \
            'reason_required'

    # A criterion that restates the objective is how a campaign gets declared a
    # success on vibes. Crude check, deliberately: it catches the copy-paste.
    refuses "open-campaign refuses a success criterion that just restates the goal" \
            open-campaign \
            '{"idempotency_key":"smoke-0066-crit","name":"smoke probe never opened","goal":"grow awareness on X","success_criterion":"Grow awareness on X","starts_on":"2026-08-01","channels":["twitter"]}' \
            'criterion_restates_goal'

    # A channel nobody registered is a channel no rollup will ever see, so the
    # campaign would be structurally unmeasurable from birth. The refusal must
    # also NAME the real platforms — a refusal that does not say what is allowed
    # just gets guessed at.
    refuses "open-campaign refuses an unregistered channel and names the real ones" \
            open-campaign \
            '{"idempotency_key":"smoke-0066-chan","name":"smoke probe never opened","goal":"reach practice owners in Pensacola","success_criterion":"three replies from practice owners by 2026-09-30","starts_on":"2026-08-01","channels":["tiktok"]}' \
            'unknown_channel'
    # The pair: the SAME refusal must carry the live platform list, or it is a
    # dead end rather than a correction.
    refuses "…and that refusal carries the registered platform list" \
            open-campaign \
            '{"idempotency_key":"smoke-0066-chan2","name":"smoke probe never opened","goal":"reach practice owners in Pensacola","success_criterion":"three replies from practice owners by 2026-09-30","starts_on":"2026-08-01","channels":["tiktok"]}' \
            'instagram'

    # ATTACHING TO A CAMPAIGN THAT WAS NEVER STATED IS THE WHOLE DEFECT. 89 pieces
    # carry a null campaign_id today; the fix is not to let content point at an
    # invented campaign, it is to make somebody write the objective down first.
    refuses "attach-to-campaign refuses a campaign nobody opened" \
            attach-to-campaign \
            '{"idempotency_key":"smoke-0066-att","campaign":"Qwertzuiop Vraxmandel Campaign","items":["https://example.invalid/p/1"]}' \
            'campaign_not_found'

    # And the absence claim must still be able to fire on the scoring side.
    refuses "score-campaign refuses to score a campaign nobody opened" \
            score-campaign \
            '{"idempotency_key":"smoke-0066-score","campaign":"Qwertzuiop Vraxmandel Campaign","base_version":1,"verdict":"worked","evidence":"none — smoke probe"}' \
            'campaign_not_found'
  fi
fi

echo
echo "passed $pass · failed $fail"
[ "$fail" -eq 0 ] || exit 1
