#!/bin/zsh
# pitr-restore-proof.sh — PROVE how recent a point production can be restored
# to, and read back how far its restore history reaches (V5-F08, RPO cell).
#
# WHY A PROOF AND NOT A READ. The database provider's API reports the project's
# history retention (history_retention_seconds), but no field anywhere names a
# "latest restorable point". "Point-in-time restore is enabled" is a setting,
# not a measurement. So this script measures it: it asks the provider for a
# disposable branch of production AS OF a past instant T, then checks that a
# probe row production shows last written at least 60 s before T is present on
# that branch, with the exact same write instant. If it is, every write up to
# the probe's instant is provably restorable, and the evaluator measures the
# exposure from THAT instant (never from T):
#
#   node mcp-server/bin/recovery-matrix-evaluate.mjs rpo out/pitr-restore-proof.json
#
# PRODUCTION IS NEVER WRITTEN, and the guards are the rehearsal's:
#   1. Every production session is opened with default_transaction_read_only=on.
#   2. The branch is created through the neon-disposable-branch metering
#      admission, with an --expires-at backstop, and deleted BY THE ID the
#      create call returned, from a trap, on every exit path. A returned id equal
#      to the default branch aborts before anything is read.
#   3. The branch session is read-only too, and its host must differ from
#      production's.
# No credential or connection string is ever printed; DSNs are derived inside
# this process and never reach an argument list shown to a human.
#
#   bin/pitr-restore-proof.sh              # attended; needs the provider API credential
#
# Output: out/pitr-restore-proof.json (the RPO cell's evidence block); exit 0
# when the probe is present on the point-in-time branch, 1 when it is not or the
# proof could not be completed.
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/usr/local/opt/node@22/bin:/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/opt/homebrew/opt/libpq/bin:/usr/local/opt/libpq/bin:/usr/local/bin:/usr/bin:/bin"
source "$REPO/bin/routine-credential-env.sh"
if [ -n "${CARR_JOB_PAYLOAD:-}" ]; then
  print -ru2 -- "pitr-restore-proof: routine dispatch refused; run it attended"
  exit 78
fi
unset NEON_API_KEY
carr_clear_routine_db_env
carr_load_routine_db_env NEON_API_KEY || exit $?

PROJECT_ID="steep-field-48688294"
PROD_BRANCH="production"
NEONCTL="$REPO/mcp-server/node_modules/.bin/neonctl"
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3 || true)"
OUT="$REPO/out/pitr-restore-proof.json"
PROBE_MARGIN_SECONDS=60
READ_ONLY="-c default_transaction_read_only=on"

say()  { print -r -- "$*"; }
die()  { print -ru2 -- "PITR PROOF FAILED: $*"; exit 1; }
iso()  { date -u -r "$1" +%Y-%m-%dT%H:%M:%SZ; }

BRANCH_ID=""
PROD_DEFAULT_ID=""
cleanup() {
  local rc=$?
  if [ -n "$BRANCH_ID" ] && [ "$BRANCH_ID" != "$PROD_DEFAULT_ID" ]; then
    if "$NEONCTL" branches delete "$BRANCH_ID" --project-id "$PROJECT_ID" >/dev/null 2>&1; then
      say "  teardown: branch $BRANCH_ID deleted"
    else
      print -ru2 -- "  teardown: COULD NOT DELETE branch $BRANCH_ID (it expires on its own); delete it by hand:"
      print -ru2 -- "            $NEONCTL branches delete $BRANCH_ID --project-id $PROJECT_ID"
    fi
  fi
  return $rc
}
trap cleanup EXIT INT TERM

[ -x "$NEONCTL" ] || die "neonctl not found at $NEONCTL (run npm ci in mcp-server/)"
command -v psql >/dev/null 2>&1 || die "psql is not on PATH"

# ── 1. retention readback, from the provider's API.
RETENTION="$("$NEONCTL" projects get "$PROJECT_ID" --output json 2>/dev/null \
  | "$PY" -c 'import json,sys; d=json.load(sys.stdin); p=d.get("project",d); v=p.get("history_retention_seconds"); print(v if isinstance(v,int) else "")')"
RETENTION_READ_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
[ -n "$RETENTION" ] || die "could not read history_retention_seconds from the provider"
say "  ok    history retention: ${RETENTION}s (read $RETENTION_READ_AT)"

# ── 2. metering admission and the default branch id teardown checks against.
BRANCH_LIST_JSON="$("$NEONCTL" branches list --project-id "$PROJECT_ID" --output json 2>/dev/null)"
PROD_DEFAULT_ID="$(print -r -- "$BRANCH_LIST_JSON" \
  | "$PY" -c 'import json,sys; print(next((b["id"] for b in json.load(sys.stdin) if b.get("default")), ""))' 2>/dev/null)"
[ -n "$PROD_DEFAULT_ID" ] || die "cannot list branches — is the provider credential loaded?"
ACTIVE_NONDEFAULT="$(print -r -- "$BRANCH_LIST_JSON" \
  | "$PY" -c 'import json,sys; print(sum(1 for b in json.load(sys.stdin) if not b.get("default")))' 2>/dev/null)"
"$PY" "$REPO/ops/platform-metering-gate.py" --gate neon-disposable-branch \
  --requested-lifetime-minutes 60 --active-nondefault-branches "$ACTIVE_NONDEFAULT" \
  --cleanup-registered >/dev/null \
  || die "neon-disposable-branch metering admission refused"

# ── 3. the point-in-time branch at T = now - 120 s.
NOW_EPOCH="$(date +%s)"
T_EPOCH=$(( NOW_EPOCH - 120 ))
T_ISO="$(iso "$T_EPOCH")"
EXPIRES_ISO="$(iso $(( NOW_EPOCH + 3600 )))"
BRANCH_JSON="$("$NEONCTL" branches create --project-id "$PROJECT_ID" \
  --name "pitr-proof-$(date -u +%Y%m%dT%H%M%SZ)" --parent "$T_ISO" --expires-at "$EXPIRES_ISO" \
  --output json 2>/dev/null)"
BRANCH_ID="$(print -r -- "$BRANCH_JSON" | "$PY" -c 'import json,sys; d=json.load(sys.stdin); b=d.get("branch",d); print(b.get("id",""))' 2>/dev/null)"
[ -n "$BRANCH_ID" ] || die "could not create the point-in-time branch at $T_ISO"
if [ "$BRANCH_ID" = "$PROD_DEFAULT_ID" ]; then
  BRANCH_ID=""   # never hand the default branch to the teardown delete
  die "create returned the default branch id; refusing"
fi
say "  ok    disposable branch $BRANCH_ID as of $T_ISO (expires $EXPIRES_ISO)"

PROD_URL="$("$NEONCTL" connection-string "$PROD_BRANCH" --project-id "$PROJECT_ID" \
            --role-name neondb_owner --database-name neondb 2>/dev/null)"
BRANCH_URL="$("$NEONCTL" connection-string "$BRANCH_ID" --project-id "$PROJECT_ID" \
              --role-name neondb_owner --database-name neondb 2>/dev/null)"
[ -n "$PROD_URL" ] && [ -n "$BRANCH_URL" ] || die "could not obtain connection strings"
PROD_HOST="${${PROD_URL#*@}%%/*}"; BRANCH_HOST="${${BRANCH_URL#*@}%%/*}"
[ "${PROD_HOST%%\?*}" != "${BRANCH_HOST%%\?*}" ] || die "branch endpoint resolves to production's host; refusing"

# ── 4. the probe: the newest ops.run row production shows last written at
#    least PROBE_MARGIN_SECONDS before T. observed_at is set to now() on every
#    insert and update, so (id, observed_at) names the exact transaction.
PROBE="$(PGOPTIONS="$READ_ONLY" psql "$PROD_URL" -v ON_ERROR_STOP=1 -At -F '|' -v cutoff="$T_ISO" -v margin="$PROBE_MARGIN_SECONDS" <<'SQL' 2>/dev/null
select id, to_char(observed_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
  from ops.run
 where observed_at <= (:'cutoff')::timestamptz - make_interval(secs => :'margin'::int)
 order by observed_at desc, id desc
 limit 1;
SQL
)"
PROBE_ID="${PROBE%%|*}"; PROBE_AT="${PROBE#*|}"
[ -n "$PROBE_ID" ] && [ -n "$PROBE_AT" ] && [ "$PROBE_ID" != "$PROBE" ] || die "production has no ops.run row before $T_ISO to probe with"

PRESENT="$(PGOPTIONS="$READ_ONLY" psql "$BRANCH_URL" -v ON_ERROR_STOP=1 -At -v pid="$PROBE_ID" -v pat="$PROBE_AT" <<'SQL' 2>/dev/null
select exists(select 1 from ops.run where id = (:'pid')::uuid and observed_at = (:'pat')::timestamptz);
SQL
)"
case "$PRESENT" in t) PRESENT_JSON=true ;; f) PRESENT_JSON=false ;; *) die "could not read the probe on the branch" ;; esac
say "  probe: ops.run row written $PROBE_AT — present on the $T_ISO branch: $PRESENT_JSON"

# ── 5. the evidence block.
mkdir -p "$REPO/out"
"$PY" - "$OUT" "$RETENTION" "$RETENTION_READ_AT" "$T_ISO" "$PROBE_AT" "$PRESENT_JSON" <<'PY'
import json, sys
out, retention, read_at, t_iso, probe_at, present = sys.argv[1:]
json.dump({
    "source": "pitr_branch_proof",
    "history_retention_seconds": int(retention),
    "retention_read_at": read_at,
    "requested_restorable_point": t_iso,
    "probe_committed_at": probe_at,
    "probe_present_on_branch": present == "true",
    "proof_target_kind": "disposable_branch",
}, open(out, "w"), indent=2, sort_keys=True)
PY
say "  evidence: $OUT"
say "  evaluate: node mcp-server/bin/recovery-matrix-evaluate.mjs rpo $OUT"
[ "$PRESENT_JSON" = true ] || exit 1
exit 0
