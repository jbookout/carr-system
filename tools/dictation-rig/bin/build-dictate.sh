#!/bin/sh
# build-dictate.sh — the one way quill-dictate gets built. Same toolchain
# guard as build-quill.sh: this Mac's CommandLineTools ships without its own
# libc++ headers, so CPLUS_INCLUDE_PATH must point at the SDK's before ANY
# swift build in this repo (harmless for pure-Swift targets, fatal to skip on
# C++-bearing ones — keeping it uniform means nobody has to remember which
# is which). Never run a bare `swift build` from docs.
set -eu

DICTATE_DIR="$(cd "$(dirname "$0")/../dictate" && pwd)"
CPLUS_INCLUDE_PATH="$(xcrun --show-sdk-path)/usr/include/c++/v1"
export CPLUS_INCLUDE_PATH

cd "$DICTATE_DIR"
swift build -c release

# SIGN WITH A STABLE IDENTITY (added 2026-08-08). macOS pins an Accessibility
# grant to the binary's Designated Requirement. Unsigned/ad-hoc binaries get a
# cdhash-based DR, so EVERY rebuild invalidated the grant and cost Joe a
# remove-and-re-add in System Settings — six times in one day's work. Signed
# with a self-signed cert, the DR becomes
#     identifier "com.carr.quill-dictate" and certificate leaf = H"<cert>"
# and neither half changes when the code does, so the grant survives rebuilds.
# The identifier is pinned explicitly rather than defaulted from the filename,
# so renaming the binary can never silently break the grant either.
#
# The identity lives in the login keychain as "CARR Quill Dictate" (a
# self-signed code-signing cert; no Apple Developer account involved). Signing
# is BEST-EFFORT on purpose: a machine without the identity — Dell's, or a
# fresh checkout — still gets a working build, it just inherits the old
# re-grant-per-rebuild behaviour. Recreate the identity with Keychain Access >
# Certificate Assistant > Create a Certificate (type: Code Signing).
SIGN_ID="CARR Quill Dictate"
BIN=".build/release/quill-dictate"
if security find-certificate -c "$SIGN_ID" >/dev/null 2>&1; then
    codesign -s "$SIGN_ID" -i com.carr.quill-dictate --force "$BIN"
    codesign --verify "$BIN" && echo "signed with stable identity: $SIGN_ID"
else
    echo "WARN no '$SIGN_ID' signing identity in the keychain — built UNSIGNED;" >&2
    echo "     the Accessibility grant will need re-adding after this build." >&2
fi

ls -la "$BIN"
echo "quill-dictate built at $DICTATE_DIR/$BIN"
