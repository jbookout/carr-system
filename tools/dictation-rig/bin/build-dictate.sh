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
ls -la .build/release/quill-dictate
echo "quill-dictate built at $DICTATE_DIR/.build/release/quill-dictate"
