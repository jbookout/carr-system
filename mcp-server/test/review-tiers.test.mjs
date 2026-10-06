// The review-tier map inside the Worker: the generated module, its reader, and
// the source-merge controller that consumes it.
//
// Three properties, the JS half of ops/review-tiers-selftest.py:
//   1. NO LOOSENING (rule a6e6ab4e): every path the merge controller refused
//      before the map existed (captured by calling the old code, see
//      ops/fixtures/review-tiers/pre-change-baseline.v1.json) is still refused.
//   2. CROSS-LANGUAGE CONSISTENCY: this reader's tier and noise decision equal
//      the Python reader's for every vector in tier-vectors.v1.json, and the
//      controller refuses exactly the tier-3 paths.
//   3. PARITY: the generated module carries the committed map unchanged.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  REVIEW_TIERS,
  REVIEW_TIERS_SOURCE,
  isReviewNoise,
  reviewTierForPath,
  reviewTierForPaths,
} from "../src/review-tiers.js";
import { evaluateSourceMerge } from "../src/source-merge-policy.js";

const readRepoJson = relative => JSON.parse(readFileSync(new URL(`../../${relative}`, import.meta.url), "utf8"));
const BASELINE = readRepoJson("ops/fixtures/review-tiers/pre-change-baseline.v1.json");
const VECTORS = readRepoJson("ops/fixtures/review-tiers/tier-vectors.v1.json").vectors;
const MAP = readRepoJson(REVIEW_TIERS_SOURCE);

function mergeRefuses(filename) {
  const result = evaluateSourceMerge({ pull_request: { files: [{ filename, status: "modified" }] } });
  return result.reason_codes.includes("protected_source_authority_boundary");
}

test('extracted verb declarations and execution retain tools.js protection', () => {
  const modules = ['tool-execution', 'tool-registry', 'versioned-write', 'verb-support',
    'activity-tools', 'campaign-tools', 'deal-room-tools', 'deal-tools', 'decision-tools',
    'document-tools', 'doc-outcome-cards-tools', 'gate-zero-tools', 'industry-event-tools', 'introspection-tools',
    'lead-tools', 'loop-tools', 'party-tools', 'rule-tools', 'search-tools', 'workspace-tools'];
  for (const module of modules) {
    const path = `mcp-server/src/${module}.js`;
    assert.equal(reviewTierForPath(path), 3, path);
    assert.equal(mergeRefuses(path), true, path);
  }
});

test("every path the controller refused before the map is still refused", () => {
  assert.ok(BASELINE.merge_protected.length > 0);
  const loosened = BASELINE.merge_protected.filter(path => !mergeRefuses(path));
  assert.deepEqual(loosened, []);
});

test("the JS reader agrees with the Python reader on every vector", () => {
  assert.ok(VECTORS.length > 0);
  for (const { path, tier, noise } of VECTORS) {
    assert.equal(reviewTierForPath(path), tier, path);
    assert.equal(isReviewNoise(path), noise, path);
  }
});

test("the controller refuses exactly the tier-3 paths", () => {
  for (const { path, tier } of VECTORS) assert.equal(mergeRefuses(path), tier >= 3, path);
  for (const path of BASELINE.paths) assert.equal(mergeRefuses(path), reviewTierForPath(path) >= 3, path);
});

test("a change set takes its highest tier", () => {
  assert.equal(reviewTierForPaths([]), MAP.default_tier);
  assert.equal(reviewTierForPaths(["README.md", "mcp-server/src/tour-map-route-state.js"]), 2);
  assert.equal(reviewTierForPaths(["README.md", "CLAUDE.md"]), 3);
  const result = evaluateSourceMerge({ pull_request: { files: [
    { filename: "README.md", status: "modified" },
    { filename: "hooks/lint-gate.py", status: "modified" },
  ] } });
  assert.ok(result.reason_codes.includes("protected_source_authority_boundary"));
});

test("the generated module carries the committed map unchanged", () => {
  assert.equal(REVIEW_TIERS_SOURCE, "ops/config/review-tiers.v1.json");
  assert.equal(REVIEW_TIERS.default_tier, MAP.default_tier);
  assert.deepEqual(REVIEW_TIERS.tunable_scalars, MAP.tunable_scalars);
  const strip = rows => rows.map(({ why, ...rest }) => rest);
  assert.deepEqual(REVIEW_TIERS.rules.map(row => ({ ...row })), strip(MAP.rules));
  assert.deepEqual(REVIEW_TIERS.noise_exclusions.map(row => ({ ...row })), strip(MAP.noise_exclusions));
  assert.deepEqual(REVIEW_TIERS.never_exclude.map(row => ({ ...row })), strip(MAP.never_exclude));
});

test("a path that is not a string is refused rather than treated as tier 1", () => {
  assert.equal(reviewTierForPath(undefined), 3);
  assert.equal(reviewTierForPath(""), 3);
  assert.ok(mergeRefuses(""));
});
