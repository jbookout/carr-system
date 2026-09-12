#!/bin/zsh
# gate-zero-canary.sh — the no-op job whose only product is proof that the
# scheduler ran it.
#
# WHAT IT IS FOR. Gate Zero's fourth predecessor step,
# `step:scheduler-active-receipt`, asks a question no ordinary job answers:
# is the scheduler REALLY running? The reader that judges it —
# mcp-server/src/gate-zero-seam-readers.v5.js, `deriveSchedulerCanary` — wants
# three clauses out of the Control Plane ledger:
#
#   receipt_binding             the dispatch row carries a non-null
#                               ops.run.evidence_ref AND was written by
#                               bin/run-scheduled.sh with source_kind wrapper
#   canary_match                the observation is of THIS run — same run_key,
#                               same evidence_ref
#   observation_after_dispatch  observed_at is STRICTLY after started_at
#
# WHY A DEDICATED JOB RATHER THAN WATCHING A REAL ONE. A real job's row mixes
# two facts — "the scheduler fired" and "the work succeeded" — and a red row
# cannot be read for the first without knowing the second. This job has no
# work to fail at, so its row is a clean statement about the scheduler alone.
#
# WHY IT IS THE SMALLEST THING THAT CAN BE WRITTEN. It touches no database, no
# network, no record layer, no file outside the receipt path it is handed. It
# mints a receipt token and exits 0. Anything else it did would be something
# else that could break, and a canary that can fail for its own reasons is a
# second job to debug rather than a signal.
#
# MEASURED 2026-09-11, against production, and it is why this file exists:
# 28,309 rows in ops.run, 21,894 of them written by the wrapper, and ZERO
# carrying an evidence_ref. Not one run in the ledger's history could have
# satisfied `receipt_binding`, canary or otherwise.
#
#   usage: bin/gate-zero-canary.sh <receipt-file>
#
# It is invoked through the wrapper, which reads that same path back:
#
#   bin/run-scheduled.sh --evidence-ref-file /tmp/gate-zero-canary.receipt \
#     gate-zero-canary gatezero.canary \
#     /bin/zsh {{REPO}}/bin/gate-zero-canary.sh /tmp/gate-zero-canary.receipt
#
# THE RECEIPT IS FRESH EVERY RUN, on purpose. A constant token would satisfy
# `receipt_binding` and `canary_match` forever, including on a run that never
# happened, because yesterday's row would match today's query just as well.
# The token carries the UTC instant and 16 random hex characters, so a row
# names the dispatch it belongs to and nothing else.
#
# EXIT CODES. 0 when the receipt was written; 64 (EX_USAGE) when no path was
# given; 74 (EX_IOERR) when the path could not be written. The last two are
# real failures and the wrapper will record them as such: a canary that could
# not mint its receipt has not proven anything, and must not read as a run
# that did.

set -u

EX_USAGE=64
EX_IOERR=74

if [ "$#" -ne 1 ] || [ -z "$1" ]; then
  print -ru2 -- "usage: bin/gate-zero-canary.sh <receipt-file>"
  exit $EX_USAGE
fi

RECEIPT_FILE="$1"

# [A-Za-z0-9:._-] only, which is exactly what the wrapper's whitelist accepts
# and what the reader's run-key pattern allows. `date` supplies the ordering,
# the hex supplies the uniqueness, and neither can contain a character that
# would be dropped on the way to ops.run.evidence_ref.
stamp="$(date -u '+%Y%m%dT%H%M%SZ')"
nonce="$(od -An -N8 -tx1 /dev/urandom 2>/dev/null | tr -d ' \n')"
if [ -z "$nonce" ]; then
  print -ru2 -- "gate-zero-canary: no entropy source; refusing to mint a guessable receipt"
  exit $EX_IOERR
fi
token="gatezero.canary:${stamp}:${nonce}"

mkdir -p -- "${RECEIPT_FILE:h}" 2>/dev/null
if ! print -r -- "$token" > "$RECEIPT_FILE" 2>/dev/null; then
  print -ru2 -- "gate-zero-canary: could not write receipt to $RECEIPT_FILE"
  exit $EX_IOERR
fi

# The one line this job prints. It goes to whatever StandardOutPath the plist
# already names; the wrapper never reads it.
print -r -- "gate-zero-canary receipt=$token"
exit 0
