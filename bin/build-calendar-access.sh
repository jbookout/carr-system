#!/bin/sh
# build-calendar-access.sh — compile and sign "CARR Calendar Access.app".
#
# WHY A BUILD STEP EXISTS AT ALL. This bundle used to ship whole in git: a zsh
# script as its main executable, plus a committed _CodeSignature. Both halves
# broke on a second Mac, and neither failure said so plainly.
#
#   1. SIGNATURE. An ad-hoc signature is made against exact bytes on the machine
#      that signed it. Checked out elsewhere it verifies as "code or signature
#      have been modified", and macOS refuses the launch.
#   2. EXECUTABLE FORMAT. macOS 26 will not launch an app bundle whose main
#      executable is a script; Launch Services answers -10669 and the app never
#      runs. Measured 2026-08-18 on macOS 26.5.2 against two throwaway bundles
#      differing only in that: script -> -10669, Mach-O -> launches.
#
# So the per-machine artifacts are BUILT per machine and the repo tracks only
# sources: tools/calendar-access-stub.c and the bundle's Info.plist and
# Contents/Resources/run.zsh. The compiled binary and the signature are ignored.
#
# Idempotent and cheap — bin/calendar-eventkit-capture.sh calls it automatically
# when the bundle is missing or its signature does not verify, so no one has to
# remember this file exists.
#
#   bin/build-calendar-access.sh
set -u

REPO="${CARR_REPO:-$HOME/carr-system}"
APP="$REPO/tools/CARR Calendar Access.app"
SRC="$REPO/tools/calendar-access-stub.c"
BIN="$APP/Contents/MacOS/carr-calendar-access"

[ -d "$APP" ] || { echo "build-calendar-access: FAIL no bundle at $APP" >&2; exit 1; }
[ -f "$SRC" ] || { echo "build-calendar-access: FAIL no source at $SRC" >&2; exit 1; }
[ -f "$APP/Contents/Resources/run.zsh" ] || {
  echo "build-calendar-access: FAIL bundle is missing Contents/Resources/run.zsh" >&2; exit 1; }

command -v clang >/dev/null 2>&1 || {
  echo "build-calendar-access: FAIL no clang. Install the Xcode command line tools:" >&2
  echo "  xcode-select --install" >&2
  exit 1
}

mkdir -p "$APP/Contents/MacOS"
clang -O2 -Wall -o "$BIN" "$SRC" || {
  echo "build-calendar-access: FAIL compile failed" >&2; exit 1; }
chmod +x "$BIN"

# Ad-hoc is the right identity here: this never leaves the Mac that built it,
# and re-signing is what makes the bundle valid for THIS machine's TCC record.
codesign --force --sign - "$APP" >/dev/null 2>&1 || {
  echo "build-calendar-access: FAIL could not sign the bundle" >&2; exit 1; }
codesign -v "$APP" 2>/dev/null || {
  echo "build-calendar-access: FAIL the signature does not verify after signing" >&2; exit 1; }

echo "build-calendar-access: OK — Mach-O stub built and bundle signed ad-hoc"
