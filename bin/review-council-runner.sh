#!/bin/zsh
# review-council-runner.sh — Automatic Review Council (built 2026-08-06;
# Grok lane added same day — see SCOPE below).
#
# Sweeps ~/carr-system/out/review-council/requests/*.json (file-per-drop work
# orders — see pipelines/run_codex_review.py's module docstring for THE FULL
# REQUEST CONTRACT SCHEMA, documented there and ONLY there, so this file does
# not carry a second copy that can drift out of sync with the one that
# actually validates it) and hands each one to pipelines/run_codex_review.py,
# one process per request. That script does the real work per request:
#   (a) for kind=code, ONE detached read-only git worktree of
#       evidence.commit_sha, shared by every reviewer the request names
#   (b) for EACH reviewer named — "codex", "grok", or both — independently:
#       probes for that reviewer's CLI; reports INSTALL NEEDED and records a
#       SKIP for that reviewer only (never installs anything, never blocks a
#       different reviewer on the same request)
#   (c) invokes that reviewer's CLI headless, sandboxed read-only, against
#       the SAME fixed, versioned review prompt (byte-for-byte identical
#       regardless of reviewer) carrying the lenses/acceptance
#       criteria/evidence, demanding STRUCTURED JSON output
#   (d) POSTs the result via curl to the Worker as THAT reviewer's OWN
#       bearer (codex-reviewer or grok-reviewer) calling record-finding —
#       the reviewers' ONLY write
#   (e) once every named reviewer has been attempted, moves the request to
#       requests/done/ or requests/failed/ with a status sidecar naming each
#       reviewer's own outcome, or leaves it in place (SKIP) only if every
#       named reviewer was unavailable
#
# SCOPE (history worth keeping straight): ORIGINALLY FROZEN to Codex-only
# automation, subscription-covered via ChatGPT-token auth
# (~/.codex/auth.json) — Grok manual per Joe's 2026-08-06 cost override,
# decision 65468572. EXTENDED the same day, before this build finished: Grok
# Build CLI 0.2.118 installed, `grok login` (OAuth device flow) completed,
# subscription-covered headless path live-verified (no XAI_API_KEY; OIDC
# entitlement token only) — independently re-confirmed live from this
# machine, including a real write attempt inside a git worktree that
# `--sandbox read-only` kernel-blocked with no --always-approve present (full
# verification log in pipelines/run_codex_review.py's module docstring). Both
# reviewers are now equally in scope; a request may name either or both.
#
# DELIBERATELY NOT DONE BY THIS FILE, ON PURPOSE:
#   - launchd registration. List-before-create is the parent session's own
#     step; this script is meant to be invoked by hand or by a task that
#     already exists, never to register itself.
#   - any deploy of the Worker changes this build made (mcp-server/src/*.js,
#     the REVIEW_TOKENS secret, pipelines/provision-review-council.sql). All
#     three are prepared and NOT applied — see that SQL file's own header and
#     this script's PROVISIONING block below for the exact human steps left.
#   - installing the Codex or Grok CLI. See run_codex_review.py's
#     find_codex_binary() / find_grok_binary().
#
# ══════════════════════════════════════════════════════════════════════════
# PROVISIONING (JOE ONLY for any step touching a secret or a deploy — an
# agent session is blocked from production writes and from ever holding a
# secret value):
#   1. Generate BOTH tokens (one per reviewer):
#        openssl rand -hex 32     # codex-reviewer
#        openssl rand -hex 32     # grok-reviewer
#   2. Put them in the Worker as REVIEW_TOKENS, a JSON map keyed by actor slug
#      (same shape as PROBE_TOKENS / INGEST_TOKENS) — REPLACES the whole map,
#      BOTH keys pasted together:
#        cd ~/carr-system/mcp-server
#        wrangler secret put REVIEW_TOKENS
#        # paste: {"codex-reviewer":"<token 1>","grok-reviewer":"<token 2>"}
#      (Both reviewers are in scope as of the 2026-08-06 extension — see
#      SCOPE above. If Grok is ever pulled back to manual-only, drop its key
#      from this map; that alone is sufficient, since reviewActorFor in
#      index.js only ever authenticates a slug present in this map.)
#   3. Insert the actor rows (both active) — prepared, NOT run, as
#      pipelines/provision-review-council.sql. Apply it through db-tap (never
#      a raw psql command substitution):
#        cd ~/carr-system && .venv/bin/python tools/db-tap.py sql pipelines/provision-review-council.sql
#      Without these rows, record-finding calls from either reviewer token
#      refuse with actor_not_provisioned even though the token authenticates
#      fine.
#   4. Add BOTH tokens from step 1 to this suite's env file:
#        # ~/.config/carr/mcp-tokens.env (600, outside the repo)
#        CARR_MCP_REVIEW_TOKEN_CODEX=<token 1>
#        CARR_MCP_REVIEW_TOKEN_GROK=<token 2>
#   5. Deploy the Worker (classifier-gated; Joe/the parent session runs this,
#      never this script) so mcp-server/src/index.js's REVIEW_TOKENS check and
#      mcp.js's 'reviewer' profile are live. Until this happens, every request
#      this runner processes will FAIL at the record-finding POST step with an
#      auth error — that failure is visible in runner.log and in the request's
#      failed/ status sidecar, not silent.
#   6. Optional: if a run reports INSTALL NEEDED for Codex, `npm i -g
#      @openai/codex` (or confirm the Codex app's actual CLI install path) is
#      Joe's own step. Grok is already installed and logged in (confirmed
#      2026-08-06) — no install step expected for it on this machine.
#   7. launchd registration — list existing scheduled tasks before creating
#      any (`launchctl list | grep carr`, or this repo's ops/scheduled-tasks/
#      convention) — deliberately left to the parent session, not this file.
# ══════════════════════════════════════════════════════════════════════════
#
# Run by hand any time: ./bin/review-council-runner.sh
# Optional: REVIEW_COUNCIL_REQUEST=<path> to process exactly one file instead
# of sweeping the whole requests/ directory (useful for a manual retry).
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/opt/homebrew/opt/libpq/bin:/usr/local/bin:/usr/bin:/bin"

RC_DIR="$REPO/out/review-council"
REQ_DIR="$RC_DIR/requests"
DONE_DIR="$REQ_DIR/done"
FAILED_DIR="$REQ_DIR/failed"
LOG="$RC_DIR/runner.log"
PY="$REPO/.venv/bin/python"

mkdir -p "$REQ_DIR" "$DONE_DIR" "$FAILED_DIR" "$RC_DIR/worktrees"

say() { print -r -- "$(date -u '+%Y-%m-%dT%H:%M:%SZ')  $*" >> "$LOG"; }

# Reviewer credential. Same file the smoke suite uses; never inlined here.
TOKENS_ENV="${CARR_MCP_ENV:-$HOME/.config/carr/mcp-tokens.env}"
[ -f "$TOKENS_ENV" ] && { set -a; . "$TOKENS_ENV"; set +a; }

rc_total=0
processed=0

# step <label> <request-file> — mirrors bin/nightly.sh's step() exactly,
# including its exit-78-is-SKIP-not-FAIL convention (EX_CONFIG), which
# pipelines/run_codex_review.py deliberately reuses so this wrapper needs no
# new logic beyond what nightly.sh already established.
step() {
  local label="$1" reqfile="$2"
  say "START $label"
  "$PY" "$REPO/pipelines/run_codex_review.py" "$reqfile" >> "$LOG" 2>&1
  local rc=$?
  if [ "$rc" -eq 0 ]; then
    say "OK    $label"
  elif [ "$rc" -eq 78 ]; then
    say "SKIP  $label (exit 78 — every named reviewer unavailable, not installed, or no known reviewer named; left in place. Per-reviewer detail is in the SUMMARY line above and the request's .status.json sidecar.)"
  else
    say "FAIL  $label (exit $rc — at least one named reviewer failed; see the SUMMARY line above for which. A different reviewer on the same request may still have succeeded — per-reviewer independence, not all-or-nothing.)"
    rc_total=1
  fi
  return "$rc"
}

say "===== review council sweep begin ====="
cd "$REPO" || { say "FATAL cannot cd $REPO"; exit 2; }

# One-off single-file mode (manual retry), or sweep the whole requests/ dir.
if [ -n "${REVIEW_COUNCIL_REQUEST:-}" ]; then
  set -- "$REVIEW_COUNCIL_REQUEST"
else
  set --
  for f in "$REQ_DIR"/*.json(N); do
    set -- "$@" "$f"
  done
fi

if [ "$#" -eq 0 ]; then
  say "no request files in $REQ_DIR — nothing to do"
else
  for reqfile in "$@"; do
    base="$(basename "$reqfile")"
    processed=$((processed + 1))
    step "review council — $base" "$reqfile"
    rc=$?
    # Route the file per outcome. SKIP leaves it in place (not a verdict on
    # the request — it means "not configured yet" or "not this reviewer's
    # job", either way a later re-run should see it again). OK/FAIL move it
    # so a healthy sweep only ever sees fresh work next time. run_codex_review.py
    # already wrote the .status.json sidecar alongside reqfile before this
    # mv — moving both together keeps the finding-outcome record attached to
    # the request it describes.
    if [ "$rc" -eq 0 ]; then
      mv -f "$reqfile" "$DONE_DIR/" 2>>"$LOG"
      [ -f "${reqfile}.status.json" ] && mv -f "${reqfile}.status.json" "$DONE_DIR/" 2>>"$LOG"
    elif [ "$rc" -eq 78 ]; then
      : # SKIP — leave in place, sidecar (if any) stays alongside it
    else
      mv -f "$reqfile" "$FAILED_DIR/" 2>>"$LOG"
      [ -f "${reqfile}.status.json" ] && mv -f "${reqfile}.status.json" "$FAILED_DIR/" 2>>"$LOG"
    fi
  done
fi

say "processed=$processed"
if [ "$rc_total" -eq 0 ]; then
  say "===== review council sweep OK ====="
else
  say "===== review council sweep FINISHED WITH FAILURES — see above ====="
fi

# Keep the log from growing without bound — same trim convention as nightly.log.
tail -n 2000 "$LOG" > "$LOG.trim" 2>/dev/null && mv "$LOG.trim" "$LOG"
exit "$rc_total"
