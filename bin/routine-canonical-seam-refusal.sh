#!/bin/sh
# A normal runtime path reached a retired Drive projection without its required
# record/document replacement.  This is a hard failure, not a missing optional
# administrator capability: silently skipping it would falsely mark the chain
# healthy while an ordinary system function did not run.
echo "MISSING_CANONICAL_SEAM: ${1:-canonical replacement is required}" >&2
exit 69
