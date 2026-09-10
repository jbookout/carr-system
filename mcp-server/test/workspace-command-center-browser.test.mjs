// The browser half of the Home unit's evidence had no runner: dealroom/ carries no package.json,
// and ops/ci.sh's unit loop covers mcp-server, control-room and workspace only — so every
// browser-model, scope, phase, freshness and link assertion sat outside the merge gate.
//
// This file is the single CI entry for those two suites. It imports them where they already live;
// their tests, fixtures and relative paths are unchanged, and nothing is copied here. Both suites
// resolve their own paths from import.meta.url, so they behave identically under this runner.
// Added to the existing `node --test test/*.test.mjs` glob in mcp-server/package.json; no new
// check class, script or ops/ci.sh edit.
import "../../dealroom/test/workspace-command-center-model.test.mjs";
import "../../dealroom/test/workspace-command-center-static.test.mjs";
