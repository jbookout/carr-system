// The review-tier map, read inside the Worker.
//
// doctrine: engineering-workflow-sop
//
// One map decides how much review a changed path gets (engineering-workflow-sop
// section 15, "Risk tier by path"). The data is ops/config/review-tiers.v1.json,
// rendered into ./review-tiers.generated.js by ops/sync-review-tiers.py because a
// Worker has no filesystem. This reader implements the same five string
// operations as lib/review_tiers.py; ops/fixtures/review-tiers/tier-vectors.v1.json
// and both test suites hold the two readers equal.
//
// Tier 3 never blocks a human: the merge controller declines to AUTO-merge and a
// human presses merge (decision 8daefaba, code-owner review stays advisory).

import { REVIEW_TIERS, REVIEW_TIERS_DIGEST, REVIEW_TIERS_SOURCE } from "./review-tiers.generated.js";

export { REVIEW_TIERS, REVIEW_TIERS_DIGEST, REVIEW_TIERS_SOURCE };

const TOP_TIER = 3;
export const MERGE_REFUSAL_TIER = 3;

function normalize(path) {
  if (typeof path !== "string" || !path) return null;
  let normal = path.replaceAll("\\", "/");
  while (normal.startsWith("./")) normal = normal.slice(2);
  return normal || null;
}

function matches(row, path) {
  let subject = path;
  let pattern = row.pattern;
  if (row.case_insensitive === true) {
    subject = subject.toLowerCase();
    pattern = pattern.toLowerCase();
  }
  switch (row.match) {
    case "path": return subject === pattern;
    case "prefix": return subject.startsWith(pattern);
    case "suffix": return subject.endsWith(pattern);
    case "basename": return subject.slice(subject.lastIndexOf("/") + 1) === pattern;
    case "contains": return subject.includes(pattern);
    default: throw new Error(`unknown review-tier match kind ${row.match}`);
  }
}

// The highest tier of every rule matching `path`, else the default. A path
// that cannot be read is the top tier: fail toward more review.
export function reviewTierForPath(path) {
  const normal = normalize(path);
  if (normal === null) return TOP_TIER;
  let tier = REVIEW_TIERS.default_tier;
  for (const row of REVIEW_TIERS.rules) {
    if (row.tier > tier && matches(row, normal)) tier = row.tier;
  }
  return tier;
}

// A change set takes the highest tier of its paths.
export function reviewTierForPaths(paths) {
  let tier = REVIEW_TIERS.default_tier;
  for (const path of paths || []) tier = Math.max(tier, reviewTierForPath(path));
  return tier;
}

// True when `path` is dropped before a model reads a diff. Never lowers a tier.
export function isReviewNoise(path) {
  const normal = normalize(path);
  if (normal === null) return false;
  if (REVIEW_TIERS.never_exclude.some(row => matches(row, normal))) return false;
  return REVIEW_TIERS.noise_exclusions.some(row => matches(row, normal));
}
