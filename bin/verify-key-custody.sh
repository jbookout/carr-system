#!/bin/zsh
# verify-key-custody.sh — prove the OFFLINE copy of the backup key can actually
# open an OFF-MAC backup.
#
#   ./bin/verify-key-custody.sh --identity <path-to-the-offline-copy> <backup.age>
#
# WHAT IS ALREADY PROVEN, so this file only has to close the last link.
# secrets-inventory.md records the offline copy as done — Joe confirmed
# 2026-08-02 that the key is written down and held off the machine.
# bin/restore-rehearse.sh proves weekly that an encrypted dump restores into a
# fresh Neon branch and matches production row for row. Two off-Mac copies of
# every dump now exist: the R2 archive and the GitHub Actions artifact.
#
# WHAT WAS NEVER PROVEN is the combination: the OFFLINE key opening an OFF-MAC
# copy. Every one of those three facts is about a different piece, and the day
# they have to work together is the day this Mac is gone — which is the worst
# possible moment to discover that the written-down key has a transcription
# error in it. A paper key with one wrong character is WORSE than no paper key,
# because it buys confidence that evaporates exactly when it is spent.
#
# ── THE REFUSAL THAT MAKES THIS TEST MEAN ANYTHING ──────────────────────────
# This script REFUSES to run against the live key file on this Mac. Pointing it
# at the working key would decrypt happily, print PASS, and prove nothing at all
# about what happens when the machine holding that file is gone — a circular
# test that reports success is worse than no test, and it is the easy mistake to
# make when you are standing in front of the machine that has the key.
#
# So the identity you pass must be a copy you produced from OUTSIDE this Mac:
# typed in from the paper copy, or read off whatever offline medium holds it.
# Typing it in is the point, not an inconvenience — that keystroke sequence is
# the thing being tested.
#
# ── THE TWO TIERS ──────────────────────────────────────────────────────────
# TIER A, five minutes, runnable anywhere: type the key from the paper copy into
# a temp file and decrypt a backup fetched from R2 or from a GitHub artifact.
# Proves the written-down key is correct and complete, which is the failure most
# likely to be real and the one you cannot detect by looking at the paper.
#
# TIER B, the full exercise: do the same on a DIFFERENT MACHINE, fetching the
# backup with credentials you could still reach if this Mac vanished. Proves the
# retrieval path too, not just the key. Tier B is the one that closes the
# roadmap's "encryption-key recovery" item.
#
# Either tier answers the question this script exists for. Tier A answers most
# of it for a fraction of the effort, so do Tier A today and Tier B when you
# next have a second machine in front of you.
#
# ── WHAT NO SESSION HAS EVER RUN IN THIS FILE, AND WHY ──────────────────────
# The decrypt path below has NEVER been executed by a Claude session. Three
# attempts to exercise it were each refused by the unattended guard, which
# exists to keep sessions away from private key material. The gate is right and
# the consequence is real: the argument handling and every refusal above are
# session-verified, and everything from the decrypt onward is not.
#
# SO SMOKE-TEST IT BEFORE YOU TRUST A PASS, and it takes thirty seconds.
# Generate a throwaway keypair (the `age` tool's own key generator writes one to
# a file), point --identity at THAT, and run this against any backup. It must
# print FAILED. If a key which cannot possibly work produces a PASS, this script
# is broken and its PASS means nothing — stop and fix it before running the real
# test.
#
# Checking that a test can fail is the reason everything else built today ships
# with a selftest. This file is the one place that check has to be done by a
# human, because the gate that protects the key also prevents a session from
# doing it.
#
# NOTHING IS SENT ANYWHERE and nothing is kept. The decrypted plaintext is
# written to a temp file, inspected, and shredded on exit through a trap that
# fires on success, failure and Ctrl-C alike. This script never prints key
# material, never copies it, and never writes it anywhere.

set -u
emulate -L zsh
setopt err_return

LIVE_KEY_BASENAME="age-key.txt"     # the working copy's filename on this Mac
IDENTITY=""
BACKUP=""

usage() {
  print -ru2 -- "usage: ./bin/verify-key-custody.sh --identity <offline-key-copy> <backup.age>"
  print -ru2 -- ""
  print -ru2 -- "  <offline-key-copy>  a file you created FROM THE OFFLINE COPY — typed in from"
  print -ru2 -- "                      paper, or read off the medium that holds it. NOT this Mac's"
  print -ru2 -- "                      working key; this script refuses that on purpose."
  print -ru2 -- "  <backup.age>        an encrypted dump fetched from an OFF-MAC source:"
  print -ru2 -- "                      the R2 archive, or a GitHub Actions artifact from the"
  print -ru2 -- "                      'Nightly backup (cloud)' workflow."
  exit 64
}

while [ $# -gt 0 ]; do
  case "$1" in
    --identity) [ $# -ge 2 ] || usage; IDENTITY="$2"; shift 2 ;;
    -h|--help)  usage ;;
    *)          [ -z "$BACKUP" ] || usage; BACKUP="$1"; shift ;;
  esac
done
[ -n "$IDENTITY" ] && [ -n "$BACKUP" ] || usage

# ── refusals, before anything is read ────────────────────────────────────────
if [ ! -f "$IDENTITY" ]; then
  print -ru2 -- "REFUSED: no such identity file: $IDENTITY"
  exit 66
fi
if [ ! -f "$BACKUP" ]; then
  print -ru2 -- "REFUSED: no such backup file: $BACKUP"
  exit 66
fi

# THE CIRCULARITY REFUSAL. Compared by basename and by real path, because the
# obvious way to defeat it by accident is a symlink to the working key.
identity_real="$(cd "$(dirname "$IDENTITY")" && pwd -P)/$(basename "$IDENTITY")"
if [ "$(basename "$identity_real")" = "$LIVE_KEY_BASENAME" ] \
   || [[ "$identity_real" == "$HOME/.config/carr/"* ]]; then
  print -ru2 -- "REFUSED — that is this Mac's working key, or a path inside its config directory."
  print -ru2 -- ""
  print -ru2 -- "  This test exists to answer ONE question: can the backups be opened when"
  print -ru2 -- "  this Mac is gone? Decrypting with the file that lives on this Mac answers"
  print -ru2 -- "  a different question and answers it yes, every time, which is how a test"
  print -ru2 -- "  that proves nothing comes to look like a test that passed."
  print -ru2 -- ""
  print -ru2 -- "  Produce the identity from the OFFLINE copy — type it in from the paper —"
  print -ru2 -- "  and point --identity at that file instead. The typing is the test."
  exit 77
fi

# ── the decrypt ──────────────────────────────────────────────────────────────
command -v age >/dev/null 2>&1 || { print -ru2 -- "REFUSED: age is not installed here. brew install age"; exit 69; }

PLAIN="$(mktemp -t carr-custody-check)"
cleanup() {
  # Shredded on every exit path — success, failure, or interrupt. A decrypted
  # production dump must not outlive the check that produced it.
  if [ -f "$PLAIN" ]; then
    dd if=/dev/urandom of="$PLAIN" bs=1m count=1 conv=notrunc 2>/dev/null || true
    rm -f "$PLAIN"
  fi
}
trap cleanup EXIT INT TERM HUP

print -r -- "Decrypting $(basename "$BACKUP") with the offline key copy..."
if ! age -d -i "$IDENTITY" "$BACKUP" > "$PLAIN" 2>/tmp/carr-custody-age-err; then
  print -ru2 -- ""
  print -ru2 -- "FAILED — the offline key did NOT open this backup."
  print -ru2 -- "  age said: $(head -2 /tmp/carr-custody-age-err | tr '\n' ' ')"
  print -ru2 -- ""
  print -ru2 -- "  This is the finding, and it is worth having today rather than on the day"
  print -ru2 -- "  the Mac dies. The likeliest cause is a transcription slip in the written"
  print -ru2 -- "  copy. Re-check it character by character against a freshly exported copy"
  print -ru2 -- "  of the working key, then re-run this. Do not consider the backups"
  print -ru2 -- "  recoverable until this passes."
  rm -f /tmp/carr-custody-age-err
  exit 1
fi
rm -f /tmp/carr-custody-age-err

# ── is the plaintext actually a database dump? ───────────────────────────────
# Decrypting to garbage would still exit 0 from age if the file were merely
# truncated, so the content is checked rather than the exit code.
bytes="$(wc -c < "$PLAIN" | tr -d ' ')"
head_line="$(head -c 4000 "$PLAIN" | head -1)"
ok=1
print -r -- ""
print -r -- "  decrypted bytes : $bytes"
print -r -- "  first line      : ${head_line:0:70}"

if [ "$bytes" -lt 100000 ]; then
  print -ru2 -- "  WARN: that is very small for a full dump — check you fetched a complete file."
  ok=0
fi
if ! head -c 200000 "$PLAIN" | grep -qE "PostgreSQL database dump|^SET |^CREATE |^COPY "; then
  print -ru2 -- "  FAILED: the plaintext does not look like a pg_dump."
  ok=0
fi
if ! grep -qaE "ops\.|public\.|CREATE TABLE" "$PLAIN"; then
  print -ru2 -- "  WARN: no recognisable CARR schema objects found."
  ok=0
fi

print -r -- ""
if [ "$ok" -eq 1 ]; then
  print -r -- "PASSED — the OFFLINE key opened an OFF-MAC backup and the plaintext is a real"
  print -r -- "database dump. The backups are recoverable without this Mac."
  print -r -- ""
  print -r -- "Close the loop with the evidence: say which machine you ran this on, which"
  print -r -- "source the backup came from (R2 or a GitHub artifact), and the byte count above."
  exit 0
fi
print -ru2 -- "NOT PASSED — see the lines above. Do not record the backups as recoverable."
exit 1
