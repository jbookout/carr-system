#!/bin/sh
# build-quill.sh — the one way quill gets built. Wraps the toolchain quirk so
# nobody rediscovers it: this Mac's CommandLineTools ships without its own
# libc++ headers (include/c++/v1 missing), so C++ compiles inside SPM
# dependencies (FluidAudio's FastClusterWrapper) die with "'cmath' file not
# found" unless CPLUS_INCLUDE_PATH points at the SDK's libc++. Found and fixed
# 2026-08-07 (dictation-rig Phase A build session).
set -eu

QUILL_DIR="$(cd "$(dirname "$0")/../vendor/quill" && pwd)"
CPLUS_INCLUDE_PATH="$(xcrun --show-sdk-path)/usr/include/c++/v1"
export CPLUS_INCLUDE_PATH

cd "$QUILL_DIR"
swift build -c release
ls -la .build/release/quill
echo "quill built at $QUILL_DIR/.build/release/quill"
