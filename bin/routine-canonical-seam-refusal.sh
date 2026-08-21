#!/bin/sh
# A normal runtime path reached a retired Drive projection without its required
# record/document replacement.
#
# Exit 69 is BLOCKED, a third outcome the chain treats as neither OK nor FAIL
# (see the 69 branch in bin/nightly.sh). This file used to call it a hard
# failure, on the grounds that skipping quietly would mark the chain healthy
# while an ordinary system function did not run. That objection still stands and
# is still met — just not by reddening the chain. Through the store-first
# cutover fourteen steps refuse this way nightly, and a chain red every night is
# one nobody reads. So the refusal is loud instead: BLOCKED is its own word in
# out/nightly.log, the count rides the completion line, and the nightly-chain
# health row prints the backlog beside the verdict. Nothing is marked done.
echo "MISSING_CANONICAL_SEAM: ${1:-canonical replacement is required}" >&2
exit 69
