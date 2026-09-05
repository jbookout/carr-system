#!/usr/bin/env bash
# The production smoke suite has two credential modes with different contracts:
# the probe token can observe only, while a partner session still exercises the
# fixed-key write probes. Run the real orchestration against a deterministic curl
# double so a profile change cannot turn an expected refusal into a failed deploy.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SMOKE="$REPO/mcp-server/smoke-reads.sh"
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/smoke-profile.XXXXXX")"
trap 'rm -rf "$TMP_ROOT"' EXIT

cat > "$TMP_ROOT/curl" <<'CURL'
#!/usr/bin/env bash
payload=""
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-d" ]; then
    shift
    payload="${1:-}"
    break
  fi
  shift
done
printf '%s\n' "$payload" >> "$SMOKE_PROFILE_CALL_LOG"

case "$payload" in
  *'"method":"tools/list"'*)
    # An empty published tool list closes the script's feature gates. The
    # profile contract under test is the decision to issue a write call at all.
    printf '%s\n' '{"result":{"tools":[]}}'
    ;;
  *'"ref":"dental"'*)
    printf '%s\n' '{"result":"needs_disambiguation"}'
    ;;
  *'"target":"Ric"'*)
    printf '%s\n' '{"result":"needs_disambiguation V-BNK-034"}'
    ;;
  *'"query":"Hughes"'*)
    printf '%s\n' '{"result":"Hughes"}'
    ;;
  *'"query":"Jon Shaw"'*)
    printf '%s\n' '{"result":"\"connections\" Tyrer"}'
    ;;
  *'"target":"C-155"'*)
    printf '%s\n' '{"result":"Dion Moniz \"hops\":2"}'
    ;;
  *'"target":"Nobody Smokeprobe Xyzzy"'*)
    printf '%s\n' '{"result":"No counterparty history"}'
    ;;
  *'"name":"source-attribution"'*)
    printf '%s\n' '{"result":"\"lanes\" unattributed"}'
    ;;
  *'"target":"Qwertzuiop Vraxmandel"'*)
    printf '%s\n' '{"result":"No record and no graph node matches"}'
    ;;
  *'"query":"Qwertzuiop Vraxmandel"'*)
    printf '%s\n' '{"result":"\"parties\":[] \"organizations\":[]"}'
    ;;
  *'"query":"Mia Arafa"'*)
    printf '%s\n' '{"result":"C-046 C-036 \"merged\":true"}'
    ;;
  *'"name":"log-activity"'*|*'"name":"set-next-action"'*|*'"name":"complete-action"'*)
    printf '%s\n' '{"result":"\"ok\":true \"replayed\":true \"existing\":true"}'
    ;;
  *)
    printf '%s\n' '{"result":"ok"}'
    ;;
esac
CURL
chmod +x "$TMP_ROOT/curl"

pass=0
fail=0
check() {
  local label="$1"
  shift
  if "$@"; then
    printf '  ok    %s\n' "$label"
    pass=$((pass + 1))
  else
    printf '  FAIL  %s\n' "$label"
    fail=$((fail + 1))
  fi
}

run_smoke() {
  local mode="$1" output="$2" calls="$3"
  : > "$calls"
  if [ "$mode" = probe ]; then
    env -i PATH="$TMP_ROOT:$PATH" HOME="$TMP_ROOT" \
      SMOKE_PROFILE_CALL_LOG="$calls" CARR_MCP_ENV="$TMP_ROOT/no-env" \
      CARR_MCP_PROBE_TOKEN=probe-token SMOKE_REPS=1 SMOKE_REP_SLEEP=0 \
      SMOKE_CALL_ATTEMPTS=1 SMOKE_CALL_RETRY_SLEEP=0 \
      bash "$SMOKE" > "$output" 2>&1
  else
    env -i PATH="$TMP_ROOT:$PATH" HOME="$TMP_ROOT" \
      SMOKE_PROFILE_CALL_LOG="$calls" CARR_MCP_ENV="$TMP_ROOT/no-env" \
      CARR_MCP_TOKEN_JOE=partner-token SMOKE_REPS=1 SMOKE_REP_SLEEP=0 \
      SMOKE_CALL_ATTEMPTS=1 SMOKE_CALL_RETRY_SLEEP=0 \
      bash "$SMOKE" > "$output" 2>&1
  fi
}

has_call() {
  local calls="$1" needle="$2"
  grep -Fq "$needle" "$calls"
}

printf 'smoke profile contract -> %s\n\n' "$SMOKE"

PROBE_OUT="$TMP_ROOT/probe.out"
PROBE_CALLS="$TMP_ROOT/probe.calls"
run_smoke probe "$PROBE_OUT" "$PROBE_CALLS"
probe_rc=$?
check "probe-mode smoke completes without treating policy as a failure" test "$probe_rc" -eq 0
check "probe mode never calls log-activity" \
  bash -c '! grep -Fq '\''"name":"log-activity"'\'' "$1"' _ "$PROBE_CALLS"
check "probe mode never calls set-next-action" \
  bash -c '! grep -Fq '\''"name":"set-next-action"'\'' "$1"' _ "$PROBE_CALLS"
check "probe mode never calls complete-action" \
  bash -c '! grep -Fq '\''"name":"complete-action"'\'' "$1"' _ "$PROBE_CALLS"
check "probe output explicitly skips the fixed-key write path" \
  grep -Fq 'SKIP  write path' "$PROBE_OUT"
check "probe output explicitly skips the completion path" \
  grep -Fq 'SKIP  completion path' "$PROBE_OUT"
check "probe output explicitly skips the analysis path" \
  grep -Fq 'SKIP  analysis path' "$PROBE_OUT"

PARTNER_OUT="$TMP_ROOT/partner.out"
PARTNER_CALLS="$TMP_ROOT/partner.calls"
run_smoke partner "$PARTNER_OUT" "$PARTNER_CALLS"
partner_rc=$?
check "partner-mode smoke completes" test "$partner_rc" -eq 0
check "partner mode retains the fixed-key log-activity probe" \
  has_call "$PARTNER_CALLS" 'smoke-write-probe-permanent'
check "partner mode retains the set-next-action probe" \
  has_call "$PARTNER_CALLS" 'smoke-ball-probe-permanent'
check "partner mode retains the complete-action probe" \
  has_call "$PARTNER_CALLS" 'smoke-complete-probe-permanent'
check "partner mode retains the auto-edge probe" \
  has_call "$PARTNER_CALLS" 'smoke-links-probe-permanent'
check "partner mode retains the analysis probe" \
  has_call "$PARTNER_CALLS" 'smoke-analysis-probe-permanent'

printf '\npassed %s · failed %s\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
