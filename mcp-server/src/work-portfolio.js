// DoctorCRE v5 slice V5-S00, source increment 1: the typed portfolio hierarchy
// as pure deterministic source. No database, no verb, no migration, no registry
// entry — persistence and the read verb are a separate increment.
//
// Validates one portfolio revision against a closed contract (21 master
// milestone nodes, four immutable child-program references, an acyclic parent
// hierarchy and an acyclic dependency DAG) and derives one deterministic digest
// over a versioned closed preimage. Canonicalization and hashing come from
// artifact-trust.js; this file does not reimplement either.
//
// A revision carrying an executable-effect field is refused, not ignored: the
// authority argument for this substrate is that a portfolio is inert until a
// human accepts its exact hash through a separate path.

import { canonicalJson, digest } from "./artifact-trust.js";

export const PORTFOLIO_REVISION_SCHEMA_VERSION = "doctorcre-v5-portfolio-revision.v1";
export const MASTER_NODE_COUNT = 21;
export const CHILD_PROGRAM_COUNT = 4;

// Immutable identity from the accepted r7 design, not configuration: a rename
// or reorder is a different portfolio.
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

const NODE_REQUIRED_KEYS = Object.freeze(["node_ref", "node_kind", "ordinal", "parent_ref"]);
const NODE_OPTIONAL_KEYS = Object.freeze([
  "authority_class", "effect_class", "data_class",
  "budget_identity", "budget_ceiling",
  "model_floor", "recovery_ref", "terminal_predicate",
]);
const NODE_KEYS = Object.freeze([...NODE_REQUIRED_KEYS, ...NODE_OPTIONAL_KEYS]);
const EDGE_KEYS = Object.freeze(["from_node_ref", "to_node_ref"]);
const MODEL_FLOOR_KEYS = Object.freeze(["provider", "model", "version", "effort"]);

// Optional metadata is never invented, but a present value must be well formed:
// an unvalidated slot is hashed into the acceptance digest as-is.
const CLASS_TOKEN = /^[a-z][a-z0-9_]{1,63}$/;
const MAX_BUDGET_CEILING = 1e12;

// Field names meaning this inert record reached into execution. Matched on the
// normalized key at every depth, so a wrapper object cannot smuggle one in.
const EFFECT_KEY_FRAGMENTS = Object.freeze([
  "ops_job", "job_id", "job_ref", "jobs",
  "capability", "envelope", "admission", "admit",
  "schedule", "scheduler", "cron",
  "acceptance", "accepted_by", "accepted_at", "acceptor",
  "outcome_receipt", "deployment", "release_ref", "j1_clock",
]);

const SHA256_REF = /^sha256:[0-9a-f]{64}$/;
// Refs span the lower-case step namespace and the upper-case Work Request one.
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
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

/** Locale-independent code-unit ordering. localeCompare varies by ICU build. */
function compareCodeUnits(a, b) {
  if (a === b) return 0;
  return a < b ? -1 : 1;
}

// JSON-safe means canonicalJson round-trips it with its meaning intact. Beyond
// the obvious unhashable types this rejects the shapes that would silently hash
// a DIFFERENT payload than the caller holds: a cycle (canonicalJson would
// recurse forever), a sparse array (holes serialize as null), and symbol,
// accessor or non-enumerable properties (Object.keys skips them, so two objects
// that differ would hash identically).
function assertJsonSafe(value, path, stack = new Set()) {
  if (value === undefined) refuse("unsupported_value", `undefined is not hashable at ${path}`, { path });
  if (value === null) return;
  const type = typeof value;
  if (type === "string" || type === "boolean") return;
  if (type === "number") {
    if (!Number.isFinite(value)) refuse("unsupported_value", `non-finite number at ${path}`, { path });
    return;
  }
  if (type === "bigint" || type === "function" || type === "symbol") {
    refuse("unsupported_value", `${type} is not hashable at ${path}`, { path });
  }
  if (stack.has(value)) {
    refuse("cyclic_input", `object graph is cyclic at ${path}; it cannot be canonicalized`, { path });
  }
  const isArray = Array.isArray(value);
  if (!isArray && !isPlainObject(value)) {
    refuse("unsupported_value", `only plain JSON objects and arrays are hashable at ${path}`, { path });
  }
  if (Object.getOwnPropertySymbols(value).length > 0) {
    refuse("unsupported_value", `symbol-keyed property at ${path} would be dropped by canonicalization`, { path });
  }
  const descriptors = Object.getOwnPropertyDescriptors(value);
  for (const [key, descriptor] of Object.entries(descriptors)) {
    if (isArray && key === "length") continue;
    if (descriptor.get !== undefined || descriptor.set !== undefined) {
      refuse("unsupported_value", `accessor property "${key}" at ${path} is not a stable value`, { path, key });
    }
    if (!descriptor.enumerable) {
      refuse("unsupported_value", `non-enumerable property "${key}" at ${path} would be dropped by canonicalization`,
        { path, key });
    }
  }
  stack.add(value);
  if (isArray) {
    for (let i = 0; i < value.length; i += 1) {
      if (!Object.prototype.hasOwnProperty.call(value, i)) {
        refuse("unsupported_value", `sparse array hole at ${path}[${i}] would serialize as null`, { path, index: i });
      }
      assertJsonSafe(value[i], `${path}[${i}]`, stack);
    }
    const indexCount = Object.keys(descriptors).filter(key => key !== "length").length;
    if (indexCount !== value.length) {
      refuse("unsupported_value", `array at ${path} carries non-index own properties`, { path });
    }
  } else {
    for (const key of Object.keys(value)) assertJsonSafe(value[key], `${path}.${key}`, stack);
  }
  stack.delete(value);
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

function assertClassToken(value, path) {
  if (typeof value !== "string" || !CLASS_TOKEN.test(value)) {
    refuse("invalid_metadata", `${path} must be a lower-case classification token`, { path, value });
  }
}

/** Type and range checks for the optional typed metadata slots. */
function validateNodeMetadata(node, path) {
  for (const key of ["authority_class", "effect_class", "data_class"]) {
    if (key in node) assertClassToken(node[key], `${path}.${key}`);
  }
  if ("budget_identity" in node) {
    if (typeof node.budget_identity !== "string" || node.budget_identity.length === 0) {
      refuse("invalid_metadata", `${path}.budget_identity must be a non-empty string`, { path: `${path}.budget_identity` });
    }
  }
  if ("budget_ceiling" in node) {
    const ceiling = node.budget_ceiling;
    if (typeof ceiling !== "number" || !Number.isFinite(ceiling) || ceiling < 0 || ceiling > MAX_BUDGET_CEILING) {
      refuse("invalid_metadata",
        `${path}.budget_ceiling must be a finite number between 0 and ${MAX_BUDGET_CEILING}`,
        { path: `${path}.budget_ceiling`, value: ceiling });
    }
  }
  if ("recovery_ref" in node) assertRef(node.recovery_ref, `${path}.recovery_ref`);
  if ("terminal_predicate" in node) {
    if (typeof node.terminal_predicate !== "string" || node.terminal_predicate.length === 0) {
      refuse("invalid_metadata", `${path}.terminal_predicate must be a non-empty string`,
        { path: `${path}.terminal_predicate` });
    }
  }
  if ("model_floor" in node) {
    const floor = node.model_floor;
    const floorPath = `${path}.model_floor`;
    if (!isPlainObject(floor)) {
      refuse("invalid_metadata", `${floorPath} must be an object`, { path: floorPath });
    }
    assertClosedKeys(floor, MODEL_FLOOR_KEYS, floorPath);
    for (const key of MODEL_FLOOR_KEYS) {
      if (!(key in floor)) {
        refuse("invalid_metadata", `${floorPath}.${key} is required once a model floor is stated`,
          { path: `${floorPath}.${key}` });
      }
      if (typeof floor[key] !== "string" || floor[key].length === 0) {
        refuse("invalid_metadata", `${floorPath}.${key} must be a non-empty string`, { path: `${floorPath}.${key}` });
      }
    }
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
    // Absent is the only way to write "not stated": an explicit null would give
    // "not stated" two distinct preimages.
    for (const key of NODE_OPTIONAL_KEYS) {
      if (key in node && node[key] === null) {
        refuse("unsupported_value", `${path}.${key} may be absent or stated, never null`, { path: `${path}.${key}` });
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
    validateNodeMetadata(node, path);
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
    const key = `${edge.from_node_ref} ${edge.to_node_ref}`;
    if (seen.has(key)) refuse("duplicate_edge", `${path} repeats an edge already declared`, { path });
    seen.add(key);
  });
}

/**
 * The parent hierarchy is a separate structure from the dependency DAG and
 * needs its own cycle check: a self-parent or a parent loop leaves every node
 * closure-valid while belonging to no portfolio.
 */
function validateParentHierarchy(nodes, knownNodes, portfolioRef) {
  const parentOf = new Map(nodes.map(node => [node.node_ref, node.parent_ref]));
  for (const node of nodes) {
    if (node.parent_ref === node.node_ref) {
      refuse("parent_cycle", `node "${node.node_ref}" is its own parent`, { cycle: [node.node_ref] });
    }
    if (node.parent_ref !== portfolioRef && !knownNodes.has(node.parent_ref)) {
      refuse("dangling_edge",
        `node "${node.node_ref}" names parent "${node.parent_ref}", which is neither the portfolio nor a node in this revision`,
        { node_ref: node.node_ref, parent_ref: node.parent_ref });
    }
    const walked = [node.node_ref];
    const seen = new Set(walked);
    let current = node.parent_ref;
    while (current !== portfolioRef) {
      if (seen.has(current)) {
        const start = walked.indexOf(current);
        refuse("parent_cycle",
          `the parent hierarchy loops: ${walked.slice(start).concat(current).join(" -> ")}`,
          { cycle: walked.slice(start) });
      }
      seen.add(current);
      walked.push(current);
      current = parentOf.get(current);
    }
  }
}

/**
 * One actual cycle from the dependency graph, by depth-first search over the
 * grey stack. Reported instead of Kahn's residual set, which also contains
 * nodes merely downstream of a cycle and would name innocent nodes as members.
 */
function findDependencyCycle(nodes, outgoing) {
  const WHITE = 0, GREY = 1, BLACK = 2;
  const colour = new Map(nodes.map(node => [node.node_ref, WHITE]));
  const stack = [];

  const visit = ref => {
    colour.set(ref, GREY);
    stack.push(ref);
    for (const next of outgoing.get(ref)) {
      const state = colour.get(next);
      if (state === GREY) return stack.slice(stack.indexOf(next));
      if (state === WHITE) {
        const found = visit(next);
        if (found) return found;
      }
    }
    stack.pop();
    colour.set(ref, BLACK);
    return null;
  };

  for (const node of nodes) {
    if (colour.get(node.node_ref) === WHITE) {
      const found = visit(node.node_ref);
      if (found) return found;
    }
  }
  return null;
}

/** Kahn's algorithm; an edge means from_node_ref precedes to_node_ref. */
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
    const ordered = new Set(order);
    const blocked = nodes.map(node => node.node_ref).filter(ref => !ordered.has(ref));
    const cycle = findDependencyCycle(nodes, outgoing) ?? [];
    refuse("cycle",
      `the dependency graph is not acyclic; cycle: ${cycle.join(" -> ")}`,
      { cycle, blocked_nodes: blocked });
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
      refuse("child_identity",
        `child-program reference ${index} must be "${CHILD_PROGRAM_REFS[index]}", saw "${ref}"`,
        { index, expected: CHILD_PROGRAM_REFS[index], actual: ref });
    }
  });
}

/**
 * Validate one portfolio revision against the closed contract and return a
 * frozen structural view. Throws PortfolioValidationError with a stable `code`.
 * Performs no I/O and mutates nothing.
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
  if (knownNodes.has(revision.portfolio_ref)) {
    refuse("invalid_ref", `revision.portfolio_ref "${revision.portfolio_ref}" is also a master node`,
      { portfolio_ref: revision.portfolio_ref });
  }
  validateEdges(revision.edges, knownNodes);
  validateParentHierarchy(revision.nodes, knownNodes, revision.portfolio_ref);
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
 * Ordering is declared, not incidental: `nodes` and `child_program_refs` are
 * ordered sets whose order carries meaning, so their order is preserved;
 * `edges` is an unordered set and is sorted here by code unit, which is the one
 * place this file reorders anything. Nothing situational is bound — no
 * timestamp, maker, reviewer or acceptance fact — so two callers describing the
 * same portfolio reach the same digest.
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
    .sort((a, b) => compareCodeUnits(a.from_node_ref, b.from_node_ref)
      || compareCodeUnits(a.to_node_ref, b.to_node_ref));
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

/** The exact canonical bytes hashed, so a reviewer can check the digest by hand. */
export function portfolioRevisionCanonicalBytes(revision) {
  return canonicalJson(portfolioRevisionPreimage(revision));
}

/**
 * The zero-effect readback projection.
 *
 * `expected_digest` is the stale-hash guard: a caller that believed it held one
 * revision is refused rather than shown another under the old name.
 * `maker_actor`/`reviewer_actor` enforce the separation the accepted contract
 * requires. Neither is persisted; this function produces no effect, which is
 * what `effects` reports.
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
    // Inert by construction: nothing here can produce an effect, and acceptance
    // remains a separate human exact-hash act.
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
