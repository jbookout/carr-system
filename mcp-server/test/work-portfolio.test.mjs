// Behavioral tests for the DoctorCRE v5 portfolio hierarchy substrate.
//
// The graph under test is the AUTHENTICATED one, not 21 invented nodes: the
// fixture was generated from the r7 constitution's dependency_dag (file digest
// sha256:403d8c7a…) and the reviewed slice catalog's portfolio_milestones_21
// (artifact digest sha256:79e46712…), with the four child-program names from the
// accepted design identity in the WR-000062 bootstrap packet. The fixture is
// TEST DATA. It is never doctrine and never an acceptance: the record layer
// remains the only authority for what the portfolio is, and no test here
// asserts that any portfolio has been accepted.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import {
  CHILD_PROGRAM_REFS,
  MASTER_NODE_COUNT,
  CHILD_PROGRAM_COUNT,
  PORTFOLIO_REVISION_SCHEMA_VERSION,
  PortfolioValidationError,
  portfolioReadback,
  portfolioRevisionCanonicalBytes,
  portfolioRevisionDigest,
  portfolioRevisionPreimage,
  validatePortfolioRevision,
} from "../src/work-portfolio.js";

const FIXTURE_PATH = fileURLToPath(new URL("./fixtures/doctorcre-portfolio-21-node.json", import.meta.url));
const FIXTURE = JSON.parse(readFileSync(FIXTURE_PATH, "utf8"));

/** A deep clone, so one test's mutation can never leak into another's fixture. */
function revision(mutate) {
  const clone = JSON.parse(JSON.stringify(FIXTURE.revision));
  if (mutate) mutate(clone);
  return clone;
}

function refusalCode(fn) {
  try {
    fn();
  } catch (error) {
    assert.ok(error instanceof PortfolioValidationError,
      `expected a PortfolioValidationError, got ${error?.name}: ${error?.message}`);
    return error.code;
  }
  return assert.fail("expected a refusal, but the call returned");
}

test("the fixture records the canonical source it was derived from", () => {
  assert.equal(FIXTURE.canonical_source.catalog_section, "5882b0cd-16f4-4896-b567-eb0fca6554f7@1");
  assert.equal(FIXTURE.canonical_source.node_identity_constitution_file_sha256,
    "sha256:403d8c7ae90c12415d2df26b13b0425003273e82f1d2f9d1cd7ecb72c03dcd64");
  assert.equal(FIXTURE.canonical_source.node_ordinal_artifact_sha256,
    "sha256:79e467125a0b5300e51f1f779b8eed2406f17cf1b085a18df96b0ad6a399fa6f");
  assert.match(FIXTURE.fixture_status, /TEST_DATA_ONLY/);
});

test("the authenticated 21-node graph validates with four children and a closed acyclic edge set", () => {
  const view = validatePortfolioRevision(revision());
  assert.equal(view.node_count, MASTER_NODE_COUNT);
  assert.equal(view.child_program_count, CHILD_PROGRAM_COUNT);
  assert.deepEqual(view.child_program_refs, CHILD_PROGRAM_REFS);
  assert.equal(view.acyclic, true);
  assert.equal(view.topological_order.length, MASTER_NODE_COUNT);
  // Prerequisites come first: the constitution node opens the order, and the
  // final reconciliation closes it.
  assert.equal(view.topological_order[0], "step:portfolio-constitution-accepted");
  assert.equal(view.topological_order.at(-1), "step:final-portfolio-outcome-reconciliation");
});

test("the topological order really respects every declared edge", () => {
  const view = validatePortfolioRevision(revision());
  const position = new Map(view.topological_order.map((ref, index) => [ref, index]));
  for (const edge of FIXTURE.revision.edges) {
    assert.ok(position.get(edge.from_node_ref) < position.get(edge.to_node_ref),
      `${edge.from_node_ref} must precede ${edge.to_node_ref}`);
  }
});

test("the digest is stable across key reordering and edge reordering", () => {
  const baseline = portfolioRevisionDigest(revision());

  const reorderedKeys = revision(draft => {
    // Rebuild every object with its keys in reverse order. Same revision, and a
    // digest that noticed would be hashing JSON layout instead of meaning.
    const reverse = value => {
      if (Array.isArray(value)) return value.map(reverse);
      if (value && typeof value === "object") {
        return Object.fromEntries(Object.keys(value).reverse().map(k => [k, reverse(value[k])]));
      }
      return value;
    };
    Object.assign(draft, reverse(draft));
  });
  assert.equal(portfolioRevisionDigest(reorderedKeys), baseline);

  const reorderedEdges = revision(draft => { draft.edges.reverse(); });
  assert.equal(portfolioRevisionDigest(reorderedEdges), baseline,
    "edges are a declared unordered set and are sorted into the preimage");

  assert.match(baseline, /^sha256:[0-9a-f]{64}$/);
});

test("node order is NOT free: reordering the ordered node set changes the digest", () => {
  const baseline = portfolioRevisionDigest(revision());
  const swapped = revision(draft => {
    const [a, b] = [draft.nodes[3], draft.nodes[4]];
    draft.nodes[3] = { ...b, ordinal: 4 };
    draft.nodes[4] = { ...a, ordinal: 5 };
  });
  assert.notEqual(portfolioRevisionDigest(swapped), baseline);
});

test("each bound source identity changes the digest when it moves", () => {
  const baseline = portfolioRevisionDigest(revision());
  const digests = new Set([baseline]);
  for (const key of ["constitution", "design", "integration", "requirements"]) {
    const moved = portfolioRevisionDigest(revision(draft => {
      draft.source_digests[key] = `sha256:${"0".repeat(64)}`;
    }));
    assert.notEqual(moved, baseline, `moving source_digests.${key} must change the revision digest`);
    assert.ok(!digests.has(moved), `source_digests.${key} must not collide with another field's change`);
    digests.add(moved);
  }
});

test("the preimage binds no timestamp, actor or acceptance fact", () => {
  const preimage = portfolioRevisionPreimage(revision());
  assert.deepEqual(Object.keys(preimage).sort(), [
    "child_program_refs", "edges", "nodes", "portfolio_ref",
    "revision_version", "schema_version", "source_digests",
  ]);
  assert.equal(preimage.schema_version, PORTFOLIO_REVISION_SCHEMA_VERSION);
  const bytes = portfolioRevisionCanonicalBytes(revision());
  for (const forbidden of ["accepted", "reviewer", "maker", "observed_at", "created_at", "timestamp"]) {
    assert.ok(!bytes.includes(`"${forbidden}"`), `the canonical bytes must not carry "${forbidden}"`);
  }
});

test("stated node metadata is bound by the digest; absent metadata is not invented", () => {
  const baseline = portfolioRevisionDigest(revision());
  const withMetadata = revision(draft => { draft.nodes[0].effect_class = "no_effect"; });
  assert.notEqual(portfolioRevisionDigest(withMetadata), baseline);
  // Absent is the only way to write "not stated": an explicit null is refused,
  // so absent and null can never mean the same thing under two digests.
  assert.equal(refusalCode(() => portfolioRevisionDigest(revision(draft => {
    draft.nodes[0].effect_class = null;
  }))), "unsupported_value");
});

test("the readback is a zero-effect projection", () => {
  const view = portfolioReadback(revision());
  assert.equal(view.accepted, false);
  assert.equal(view.effects.creates_effect, false);
  for (const [name, count] of Object.entries(view.effects)) {
    if (name === "creates_effect") continue;
    assert.equal(count, 0, `${name} must be 0 in a source-only readback`);
  }
  assert.deepEqual(view.unresolved_dependencies, []);
  assert.equal(view.revision_digest, portfolioRevisionDigest(revision()));
});

test("the readback accepts a matching expected digest and refuses a stale one", () => {
  const current = portfolioRevisionDigest(revision());
  assert.equal(portfolioReadback(revision(), { expected_digest: current }).revision_digest, current);
  assert.equal(refusalCode(() => portfolioReadback(revision(), {
    expected_digest: `sha256:${"a".repeat(64)}`,
  })), "stale_expected_digest");
});

test("distinct maker and reviewer pass; the same actor as both refuses", () => {
  assert.ok(portfolioReadback(revision(), { maker_actor: "joe", reviewer_actor: "dell" }));
  assert.equal(refusalCode(() => portfolioReadback(revision(), {
    maker_actor: "joe", reviewer_actor: "joe",
  })), "self_review");
});

test("a 22nd node and a 20-node graph both refuse on count", () => {
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.nodes.push({
      node_ref: "step:invented-extra-node", node_kind: "milestone",
      ordinal: 22, parent_ref: draft.portfolio_ref,
    });
  }))), "node_count");
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.nodes.pop();
  }))), "node_count");
});

test("a duplicated node identity refuses even when the count still reads 21", () => {
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.nodes[5].node_ref = draft.nodes[4].node_ref;
  }))), "duplicate_node");
});

test("a fifth child, a renamed child and a reordered child set all refuse", () => {
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.child_program_refs.push("a-fifth-program");
  }))), "child_count");
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.child_program_refs[1] = "assurance-fabric-v2";
  }))), "child_identity");
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.child_program_refs.reverse();
  }))), "child_identity");
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.child_program_refs[3] = draft.child_program_refs[0];
  }))), "duplicate_child");
});

test("a cycle refuses and names the nodes still in it", () => {
  let detail;
  try {
    validatePortfolioRevision(revision(draft => {
      // Point the opening node back at a downstream one, closing the loop.
      draft.edges.push({
        from_node_ref: "step:gate-zero-read-only-outcome",
        to_node_ref: "step:portfolio-constitution-accepted",
      });
    }));
    assert.fail("expected a cycle refusal");
  } catch (error) {
    assert.equal(error.code, "cycle");
    detail = error.detail;
  }
  assert.ok(detail.cycle.includes("step:portfolio-constitution-accepted"));
  assert.ok(detail.cycle.includes("step:gate-zero-read-only-outcome"));
});

test("a self-edge refuses as a cycle", () => {
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.edges.push({
      from_node_ref: "step:scheduler-active-receipt",
      to_node_ref: "step:scheduler-active-receipt",
    });
  }))), "cycle");
});

test("a dangling edge and a dangling parent both refuse", () => {
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.edges[0].from_node_ref = "step:not-a-node-in-this-revision";
  }))), "dangling_edge");
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.nodes[2].parent_ref = "step:not-a-node-in-this-revision";
  }))), "dangling_edge");
});

test("a repeated edge refuses", () => {
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.edges.push({ ...draft.edges[0] });
  }))), "duplicate_edge");
});

test("an unknown field refuses at every level rather than being dropped", () => {
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.extra_top_level = "ignored?";
  }))), "unknown_field");
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.nodes[0].extra_node_field = "ignored?";
  }))), "unknown_field");
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.edges[0].weight = 3;
  }))), "unknown_field");
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.source_digests.benchmark = `sha256:${"b".repeat(64)}`;
  }))), "unknown_field");
});

test("an executable-effect payload refuses anywhere in the revision", () => {
  for (const mutate of [
    draft => { draft.ops_job = "job-1"; },
    draft => { draft.nodes[0].capability = "engineering-writer"; },
    draft => { draft.nodes[0].terminal_predicate = { admission: "auto" }; },
    draft => { draft.nodes[1].recovery_ref = { nested: { scheduler: "hourly" } }; },
    draft => { draft.nodes[2].budget_identity = { accepted_by: "joe" }; },
  ]) {
    assert.equal(refusalCode(() => validatePortfolioRevision(revision(mutate))), "effect_payload_refused");
  }
});

test("values JSON cannot round-trip refuse before they reach the hash", () => {
  for (const [label, mutate] of [
    ["NaN", draft => { draft.nodes[0].budget_ceiling = Number.NaN; }],
    ["Infinity", draft => { draft.nodes[0].budget_ceiling = Number.POSITIVE_INFINITY; }],
    ["Date", draft => { draft.nodes[0].model_floor = new Date(0); }],
    ["function", draft => { draft.nodes[0].model_floor = () => "opus"; }],
    ["bigint", draft => { draft.nodes[0].budget_ceiling = 10n; }],
  ]) {
    // JSON.parse/JSON.stringify cannot carry these, so build them after cloning.
    const draft = JSON.parse(JSON.stringify(FIXTURE.revision));
    mutate(draft);
    assert.equal(refusalCode(() => validatePortfolioRevision(draft)), "unsupported_value",
      `${label} must refuse`);
  }
});

test("a wrong schema version, revision version, ordinal or node kind refuses", () => {
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.schema_version = "doctorcre-v5-portfolio-revision.v2";
  }))), "schema_version");
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.revision_version = 0;
  }))), "invalid_revision_version");
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.nodes[7].ordinal = 99;
  }))), "invalid_ordinal");
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.nodes[7].node_kind = "workflow";
  }))), "invalid_node_kind");
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    delete draft.nodes[7].parent_ref;
  }))), "missing_field");
  assert.equal(refusalCode(() => validatePortfolioRevision(revision(draft => {
    draft.source_digests.design = "403d8c7ae90c12415d2df26b13b0425003273e82f1d2f9d1cd7ecb72c03dcd64";
  }))), "invalid_source_digest");
});
