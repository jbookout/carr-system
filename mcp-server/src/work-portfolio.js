// DoctorCRE v5 slice V5-S00, source increment 1 of 2: the typed portfolio
// hierarchy as PURE DETERMINISTIC SOURCE. There is no database here, no verb
// registration, no migration and no registry entry, and that is the point —
// every serialized surface in this repo (the migration-number frontier, the
// SCAC mutation registry and its seals, the shared source-inventory fixture,
// the tools.js command contract) is deliberately untouched so this increment
// collides with nothing else in flight. Persistence and the read verb follow in
// a separately collision-checked increment; THIS FILE IS NOT ALL OF V5-S00.
//
// WHAT IT DOES: validates a closed typed portfolio revision — exactly 21 master
// milestone nodes, exactly four immutable child-program references, an acyclic
// closed edge set — and derives one deterministic revision digest over a
// versioned closed preimage. WHAT IT CANNOT DO, by construction: create a job,
// a capability, an execution envelope, an admission, a schedule, an acceptance
// or a J1 clock. A revision that so much as carries a field named like one of
// those is refused rather than ignored, because the whole authority argument for
// this substrate is that a portfolio is inert until a human accepts its exact
// hash through a separate path.
//
// The digest utility is imported from artifact-trust.js and is NOT reimplemented
// here: one canonicalization in the repo, not two that can drift.

import { canonicalJson, digest } from "./artifact-trust.js";

export const PORTFOLIO_REVISION_SCHEMA_VERSION = "doctorcre-v5-portfolio-revision.v1";
export const MASTER_NODE_COUNT = 21;
export const CHILD_PROGRAM_COUNT = 4;

// The four child programs are IMMUTABLE IDENTITY, not configuration. They come
// from the accepted r7 design identity; a rename is a different portfolio, so a
// renamed or reordered set is refused rather than accepted as an edit.
export const CHILD_PROGRAM_REFS = Object.freeze([
  "foundation-and-control-plane",
  "assurance-fabric",
  "product-journeys",
  "rollout-and-retirement",
]);

export const NODE_KINDS = Object.freeze(["portfolio", "child", "milestone", "slice"]);
export const SOURCE_DIGEST_KEYS = Object.freeze(["constitution", "design", "integration", "requirements"]);

const REVISION_KEYS = Object.freeze([
  "schema_version", "revision_version", "portfolio_ref", "source_digests",
  "child_program_refs", "nodes", "edges",
]);

// Required node identity, then the typed metadata slots later persistence needs.
// The metadata is OPTIONAL and no default is invented: the authenticated source
// states node identity and dependency only, so a value is bound when a reviewed
// source states one and is absent otherwise. Absent is the only way to say "not
// stated" — an explicit null is refused, so absent and null can never collide
// into two preimages that mean the same thing.
const NODE_REQUIRED_KEYS = Object.freeze(["node_ref", "node_kind", "ordinal", "parent_ref"]);
const NODE_OPTIONAL_KEYS = Object.freeze([
  "authority_class", "effect_class", "data_class",
  "budget_identity", "budget_ceiling",
  "model_floor", "recovery_ref", "terminal_predicate",
]);
const NODE_KEYS = Object.freeze([...NODE_REQUIRED_KEYS, ...NODE_OPTIONAL_KEYS]);
const EDGE_KEYS = Object.freeze(["from_node_ref", "to_node_ref"]);

// Field names that would mean this inert record had reached into execution.
// Matched on the normalized key at every depth, so a nested payload cannot
// smuggle one in under a wrapper object.
const EFFECT_KEY_FRAGMENTS = Object.freeze([
  "ops_job", "job_id", "job_ref", "jobs",
  "capability", "envelope", "admission", "admit",
  "schedule", "scheduler", "cron",
  "acceptance", "accepted_by", "accepted_at", "acceptor",
  "outcome_receipt", "deployment", "release_ref", "j1_clock",
]);

const SHA256_REF = /^sha256:[0-9a-f]{64}$/;
// Refs carry both the lower-case step namespace ("step:gate-zero-read-only-
// outcome") and the upper-case Work Request namespace ("WR-000062"), so the
// pattern is case-tolerant on purpose.
const REF = /^[A-Za-z0-9][A-Za-z0-9:._-]{2,199}$/;

export class PortfolioValidationError extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "PortfolioValidationError";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function refuse(code, message, detail) {
  throw new PortfolioValidationError(code, message, detail);
}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    && (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null);
}

// JSON-safe means: it survives canonicalJson and comes back meaning the same
// thing. undefined, NaN, Infinity, bigint, functions, symbols and Date all fail
// that test — JSON.stringify either drops them or throws or silently coerces —
// so they are refused before they can reach the hash.
function assertJsonSafe(value, path) {
  if (value === undefined) refuse("unsupported_value", `undefined is not hashable at ${path}`, { path });
  if (value === null) return;
  const t = typeof value;
  if (t === "string" || t === "boolean") return;
  if (t === "number") {
    if (!Number.isFinite(value)) refuse("unsupported_value", `non-finite number at ${path}`, { path });
    return;
  }
  if (t === "bigint" || t === "function" || t === "symbol") {
    refuse("unsupported_value", `${t} is not hashable at ${path}`, { path });
  }
  if (Array.isArray(value)) {
    value.forEach((item, i) => assertJsonSafe(item, `${path}[${i}]`));
    return;
  }
  if (!isPlainObject(value)) {
    refuse("unsupported_value", `only plain JSON objects are hashable at ${path}`, { path });
  }
  for (const key of Object.keys(value)) assertJsonSafe(value[key], `${path}.${key}`);
}

function assertNoEffectPayload(value, path) {
  if (Array.isArray(value)) {
    value.forEach((item, i) => assertNoEffectPayload(item, `${path}[${i}]`));
    return;
  }
  if (!isPlainObject(value)) return;
  for (const key of Object.keys(value)) {
    const normalized = key.toLowerCase();
    for (const fragment of EFFECT_KEY_FRAGMENTS) {
      if (normalized === fragment || normalized.endsWith(`_${fragment}`) || normalized.startsWith(`${fragment}_`)) {
        refuse("effect_payload_refused",
          `a portfolio revision may not carry executable-effect field "${key}" at ${path}`,
          { path: `${path}.${key}`, key });
      }
    }
    assertNoEffectPayload(value[key], `${path}.${key}`);
  }
}

function assertClosedKeys(object, allowed, path) {
  for (const key of Object.keys(object)) {
    if (!allowed.includes(key)) {
      refuse("unknown_field", `unknown field "${key}" at ${path}`, { path: `${path}.${key}`, key });
    }
  }
}

function assertRef(value, path) {
  if (typeof value !== "string" || !REF.test(value)) {
    refuse("invalid_ref", `expected a reference string at ${path}`, { path, value });
  }
}

function validateSourceDigests(source, path) {
  if (!isPlainObject(source)) refuse("invalid_shape", `${path} must be an object`, { path });
  assertClosedKeys(source, SOURCE_DIGEST_KEYS, path);
  for (const key of SOURCE_DIGEST_KEYS) {
    const value = source[key];
    if (typeof value !== "string" || !SHA256_REF.test(value)) {
      refuse("invalid_source_digest", `${path}.${key} must be a sha256: reference`, { path: `${path}.${key}` });
    }
  }
}

function validateNodes(nodes) {
  if (!Array.isArray(nodes)) refuse("invalid_shape", "revision.nodes must be an array", { path: "revision.nodes" });
  if (nodes.length !== MASTER_NODE_COUNT) {
    refuse("node_count", `expected exactly ${MASTER_NODE_COUNT} master nodes, saw ${nodes.length}`,
      { expected: MASTER_NODE_COUNT, actual: nodes.length });
  }
  const seen = new Map();
  nodes.forEach((node, index) => {
    const path = `revision.nodes[${index}]`;
    if (!isPlainObject(node)) refuse("invalid_shape", `${path} must be an object`, { path });
    assertClosedKeys(node, NODE_KEYS, path);
    for (const key of NODE_REQUIRED_KEYS) {
      if (!(key in node)) refuse("missing_field", `${path}.${key} is required`, { path: `${path}.${key}` });
    }
    for (const key of NODE_OPTIONAL_KEYS) {
      if (key in node && node[key] === null) {
        refuse("unsupported_value",
          `${path}.${key} may be absent or stated, never null — absent is how "not stated" is written`,
          { path: `${path}.${key}` });
      }
    }
    assertRef(node.node_ref, `${path}.node_ref`);
    assertRef(node.parent_ref, `${path}.parent_ref`);
    if (!NODE_KINDS.includes(node.node_kind)) {
      refuse("invalid_node_kind", `${path}.node_kind must be one of ${NODE_KINDS.join(", ")}`,
        { path: `${path}.node_kind`, value: node.node_kind });
    }
    if (node.ordinal !== index + 1) {
      refuse("invalid_ordinal", `${path}.ordinal must be ${index + 1}; nodes are an ordered set`,
        { path: `${path}.ordinal`, expected: index + 1, actual: node.ordinal });
    }
    if (seen.has(node.node_ref)) {
      refuse("duplicate_node", `node_ref "${node.node_ref}" appears at ordinal ${seen.get(node.node_ref)} and ${node.ordinal}`,
        { node_ref: node.node_ref });
    }
    seen.set(node.node_ref, node.ordinal);
  });
  return seen;
}

function validateEdges(edges, knownNodes) {
  if (!Array.isArray(edges)) refuse("invalid_shape", "revision.edges must be an array", { path: "revision.edges" });
  const seen = new Set();
  edges.forEach((edge, index) => {
    const path = `revision.edges[${index}]`;
    if (!isPlainObject(edge)) refuse("invalid_shape", `${path} must be an object`, { path });
    assertClosedKeys(edge, EDGE_KEYS, path);
    for (const key of EDGE_KEYS) {
      if (!(key in edge)) refuse("missing_field", `${path}.${key} is required`, { path: `${path}.${key}` });
      assertRef(edge[key], `${path}.${key}`);
    }
    if (!knownNodes.has(edge.from_node_ref)) {
      refuse("dangling_edge", `${path}.from_node_ref "${edge.from_node_ref}" is not a node in this revision`,
        { path, node_ref: edge.from_node_ref });
    }
    if (!knownNodes.has(edge.to_node_ref)) {
      refuse("dangling_edge", `${path}.to_node_ref "${edge.to_node_ref}" is not a node in this revision`,
        { path, node_ref: edge.to_node_ref });
    }
    if (edge.from_node_ref === edge.to_node_ref) {
      refuse("cycle", `${path} is a self-edge on "${edge.from_node_ref}"`, { cycle: [edge.from_node_ref] });
    }
    const key = `${edge.from_node_ref} ${edge.to_node_ref}`;
    if (seen.has(key)) refuse("duplicate_edge", `${path} repeats an edge already declared`, { path });
    seen.add(key);
  });
}

// Kahn's algorithm. An edge means from_node_ref must complete before
// to_node_ref, so the returned order lists prerequisites first. On failure the
// remaining nodes ARE the cycle, and they are reported: "not acyclic" without
// naming the members is a message nobody can act on.
function topologicalOrder(nodes, edges) {
  const indegree = new Map(nodes.map(node => [node.node_ref, 0]));
  const outgoing = new Map(nodes.map(node => [node.node_ref, []]));
  for (const edge of edges) {
    outgoing.get(edge.from_node_ref).push(edge.to_node_ref);
    indegree.set(edge.to_node_ref, indegree.get(edge.to_node_ref) + 1);
  }
  const ready = nodes.filter(node => indegree.get(node.node_ref) === 0).map(node => node.node_ref);
  const order = [];
  while (ready.length > 0) {
    const current = ready.shift();
    order.push(current);
    for (const next of outgoing.get(current)) {
      const remaining = indegree.get(next) - 1;
      indegree.set(next, remaining);
      if (remaining === 0) ready.push(next);
    }
  }
  if (order.length !== nodes.length) {
    const cycle = nodes.map(node => node.node_ref).filter(ref => !order.includes(ref));
    refuse("cycle", `the dependency graph is not acyclic; ${cycle.length} node(s) remain in a cycle`, { cycle });
  }
  return order;
}

function validateChildProgramRefs(refs) {
  if (!Array.isArray(refs)) {
    refuse("invalid_shape", "revision.child_program_refs must be an array", { path: "revision.child_program_refs" });
  }
  if (refs.length !== CHILD_PROGRAM_COUNT) {
    refuse("child_count", `expected exactly ${CHILD_PROGRAM_COUNT} child-program references, saw ${refs.length}`,
      { expected: CHILD_PROGRAM_COUNT, actual: refs.length });
  }
  const seen = new Set();
  refs.forEach((ref, index) => {
    if (typeof ref !== "string") {
      refuse("invalid_shape", `revision.child_program_refs[${index}] must be a string`, { index });
    }
    if (seen.has(ref)) refuse("duplicate_child", `child-program reference "${ref}" is repeated`, { child_ref: ref });
    seen.add(ref);
    if (ref !== CHILD_PROGRAM_REFS[index]) {
      refuse("child_identity", `child-program reference ${index} must be "${CHILD_PROGRAM_REFS[index]}", saw "${ref}"; the four children are immutable identity, not configuration`,
        { index, expected: CHILD_PROGRAM_REFS[index], actual: ref });
    }
  });
}

/**
 * Validate one portfolio revision against the closed contract and return a
 * frozen structural view. Throws PortfolioValidationError with a stable `code`
 * on any refusal. Performs no I/O and mutates nothing.
 */
export function validatePortfolioRevision(revision) {
  if (!isPlainObject(revision)) refuse("invalid_shape", "revision must be an object", { path: "revision" });
  assertJsonSafe(revision, "revision");
  assertNoEffectPayload(revision, "revision");
  assertClosedKeys(revision, REVISION_KEYS, "revision");
  for (const key of REVISION_KEYS) {
    if (!(key in revision)) refuse("missing_field", `revision.${key} is required`, { path: `revision.${key}` });
  }
  if (revision.schema_version !== PORTFOLIO_REVISION_SCHEMA_VERSION) {
    refuse("schema_version", `revision.schema_version must be "${PORTFOLIO_REVISION_SCHEMA_VERSION}"`,
      { expected: PORTFOLIO_REVISION_SCHEMA_VERSION, actual: revision.schema_version });
  }
  if (!Number.isInteger(revision.revision_version) || revision.revision_version < 1) {
    refuse("invalid_revision_version", "revision.revision_version must be an integer of at least 1",
      { actual: revision.revision_version });
  }
  assertRef(revision.portfolio_ref, "revision.portfolio_ref");
  validateSourceDigests(revision.source_digests, "revision.source_digests");
  validateChildProgramRefs(revision.child_program_refs);
  const knownNodes = validateNodes(revision.nodes);
  validateEdges(revision.edges, knownNodes);
  for (const node of revision.nodes) {
    if (node.parent_ref !== revision.portfolio_ref && !knownNodes.has(node.parent_ref)) {
      refuse("dangling_edge", `node "${node.node_ref}" names parent "${node.parent_ref}", which is neither the portfolio nor a node in this revision`,
        { node_ref: node.node_ref, parent_ref: node.parent_ref });
    }
  }
  const order = topologicalOrder(revision.nodes, revision.edges);
  return Object.freeze({
    portfolio_ref: revision.portfolio_ref,
    revision_version: revision.revision_version,
    node_count: revision.nodes.length,
    edge_count: revision.edges.length,
    child_program_count: revision.child_program_refs.length,
    acyclic: true,
    topological_order: Object.freeze(order),
    child_program_refs: Object.freeze([...revision.child_program_refs]),
  });
}

/**
 * The versioned closed preimage the revision digest is taken over.
 *
 * ORDERING IS DECLARED, NOT INCIDENTAL. `nodes` and `child_program_refs` are
 * ORDERED sets whose order carries meaning (node ordinal, child identity slot),
 * so their array order is preserved exactly. `edges` is an UNORDERED set, so it
 * is sorted here — that is the one and only place this file reorders anything,
 * and it is what makes the digest independent of how a caller happened to list
 * its edges.
 *
 * NOTHING SITUATIONAL IS BOUND: no timestamp, no maker, no reviewer, no
 * acceptance fact. Two callers describing the same portfolio at different times
 * must reach the same digest, or an exact-hash acceptance means nothing.
 */
export function portfolioRevisionPreimage(revision) {
  validatePortfolioRevision(revision);
  const nodes = revision.nodes.map(node => {
    const projected = {
      node_ref: node.node_ref,
      node_kind: node.node_kind,
      ordinal: node.ordinal,
      parent_ref: node.parent_ref,
    };
    for (const key of NODE_OPTIONAL_KEYS) {
      if (key in node) projected[key] = node[key];
    }
    return projected;
  });
  const edges = revision.edges
    .map(edge => ({ from_node_ref: edge.from_node_ref, to_node_ref: edge.to_node_ref }))
    .sort((a, b) => (a.from_node_ref === b.from_node_ref
      ? a.to_node_ref.localeCompare(b.to_node_ref)
      : a.from_node_ref.localeCompare(b.from_node_ref)));
  return {
    schema_version: PORTFOLIO_REVISION_SCHEMA_VERSION,
    revision_version: revision.revision_version,
    portfolio_ref: revision.portfolio_ref,
    source_digests: {
      constitution: revision.source_digests.constitution,
      design: revision.source_digests.design,
      integration: revision.source_digests.integration,
      requirements: revision.source_digests.requirements,
    },
    child_program_refs: [...revision.child_program_refs],
    nodes,
    edges,
  };
}

/** The deterministic `sha256:` digest of one validated portfolio revision. */
export function portfolioRevisionDigest(revision) {
  return digest(portfolioRevisionPreimage(revision));
}

/** The exact canonical bytes hashed, exposed so a reviewer can check the hash by hand. */
export function portfolioRevisionCanonicalBytes(revision) {
  return canonicalJson(portfolioRevisionPreimage(revision));
}

/**
 * The zero-effect readback projection.
 *
 * `expected_digest` is the stale-hash guard: a caller that believed it was
 * looking at one revision and is handed another is refused rather than shown
 * the new one under the old name. `maker_actor`/`reviewer_actor` enforce the
 * separation the accepted contract requires — one actor cannot both propose and
 * independently review. Neither check is stored by anything here; this function
 * persists nothing and can produce no effect, which is what `effects` reports.
 */
export function portfolioReadback(revision, options = {}) {
  if (!isPlainObject(options)) refuse("invalid_shape", "options must be an object", { path: "options" });
  assertClosedKeys(options, ["expected_digest", "maker_actor", "reviewer_actor"], "options");
  const view = validatePortfolioRevision(revision);
  const revisionDigest = portfolioRevisionDigest(revision);

  if (options.expected_digest !== undefined) {
    if (typeof options.expected_digest !== "string" || !SHA256_REF.test(options.expected_digest)) {
      refuse("invalid_expected_digest", "options.expected_digest must be a sha256: reference",
        { path: "options.expected_digest" });
    }
    if (options.expected_digest !== revisionDigest) {
      refuse("stale_expected_digest",
        "the revision no longer hashes to the expected digest; re-read it rather than acting on the stale one",
        { expected: options.expected_digest, actual: revisionDigest });
    }
  }

  const maker = options.maker_actor;
  const reviewer = options.reviewer_actor;
  if (maker !== undefined || reviewer !== undefined) {
    if (typeof maker !== "string" || maker.length === 0) {
      refuse("missing_field", "options.maker_actor is required when a reviewer is named",
        { path: "options.maker_actor" });
    }
    if (typeof reviewer !== "string" || reviewer.length === 0) {
      refuse("missing_field", "options.reviewer_actor is required when a maker is named",
        { path: "options.reviewer_actor" });
    }
    if (maker === reviewer) {
      refuse("self_review", `"${maker}" cannot be both maker and independent reviewer of the same revision`,
        { actor: maker });
    }
  }

  return Object.freeze({
    schema_version: PORTFOLIO_REVISION_SCHEMA_VERSION,
    portfolio_ref: view.portfolio_ref,
    revision_version: view.revision_version,
    revision_digest: revisionDigest,
    node_count: view.node_count,
    edge_count: view.edge_count,
    child_program_count: view.child_program_count,
    child_program_refs: view.child_program_refs,
    acyclic: view.acyclic,
    topological_order: view.topological_order,
    unresolved_dependencies: Object.freeze([]),
    accepted: false,
    // This source increment is inert on purpose. Nothing below can be anything
    // but zero until the persistence increment lands, and even then acceptance
    // is a separate human exact-hash act.
    effects: Object.freeze({
      creates_effect: false,
      jobs: 0,
      capabilities: 0,
      execution_envelopes: 0,
      admissions: 0,
      schedules: 0,
      acceptances: 0,
      deployments: 0,
    }),
  });
}
