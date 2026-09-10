// V5-F05 — the bounded read-only F01 -> F05 source adapter, proved case by case.
//
// Everything here is synthetic. Nothing reaches a database, a provider, a network
// or the filesystem: the REAL F01 store adapter is constructed over a scripted
// fake handle that records every statement and every parameter, so the suite can
// prove the properties a Node test can actually prove about a read adapter —
//
//   * which STATEMENTS travel to the database, and that no writer is among them,
//   * that the mapped record's fields come from the artifact's own hashed
//     preimage and not from custody metadata or a caller value,
//   * that everything unmappable is NAMED and blocks assembly rather than being
//     dropped from a manifest that then reads as complete,
//   * that the same store answers produce byte-identical bytes twice,
//   * that a caller cannot reach records, queries or sources by any route.
//
// NO FIXTURE NAMES A REAL THING: every digest, account, native id and evidence
// reference below is unmistakably test data.

import test from "node:test";
import assert from "node:assert/strict";

import { canonicalJson, digest } from "../src/artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import { V5_NO_EFFECTS } from "../src/global-boundaries.v5.js";
import {
  V5_F01_ARTIFACT_SCHEMA_VERSION,
  V5_F01_EVIDENCE_CLASSES,
} from "../src/record-source-authority.v5.js";
import {
  V5_F01_STORE_SCHEMA_VERSION,
  createRecordSourceAuthorityStore,
  v5F01StoreEnvelope,
} from "../src/record-source-authority-store.v5.js";
import {
  V5_F05_UNIVERSE_SCHEMA_VERSION,
  compileRuleUniverse,
} from "../src/rule-applicability.v5.js";
import {
  V5F05Error,
  V5_F05_EXTERNAL_ORIGINS,
  V5_F05_FROZEN_INPUT_SCHEMA_VERSION,
  V5_F05_MANIFEST_SCHEMA_VERSION,
  V5_F05_UNKNOWN_UPSTREAM_DERIVED_KIND,
  assembleContextManifest,
  compileTaintLineage,
  verifyContextManifest,
} from "../src/context-assembly.v5.js";
import {
  V5_F05_SOURCE_CONSUMABLE_COVERAGE_STATE,
  V5_F05_SOURCE_DERIVATIVE_LINK_KEYS,
  V5_F05_SOURCE_EVIDENCE_CLASS_MAP,
  V5_F05_SOURCE_MAX_SELECTION,
  V5_F05_SOURCE_MODE,
  V5_F05_SOURCE_STORED_COPY_NOTE,
  V5_F05_SOURCE_STORED_COPY_SOURCE_ID,
  V5_F05_SOURCE_PROJECTION_SCHEMA_VERSION,
  V5_F05_SOURCE_REFUSED_TEMPLATE_KEYS,
  V5_F05_SOURCE_RETRIEVAL_CLASS,
  V5_F05_SOURCE_SCHEMA_VERSION,
  V5_F05_SOURCE_UNMAPPED_REASONS,
  V5F05SourceError,
  assembleContextFromSource,
  contextAssemblySourceGaps,
  createContextAssemblySource,
  v5F05SourceContractCanonicalBytes,
  v5F05SourceContractDigest,
  v5F05SourceContractPreimage,
} from "../src/context-assembly-source.v5.js";

// --- synthetic stored state ------------------------------------------------

const SERVER_NOW = "2026-09-09T12:00:00.000Z";
// THE SOURCE'S OWN INSTANT, three years before the read. It is what the source
// says it saw, and importing it must not make it younger.
const SOURCE_OBSERVED = "2023-04-11T08:30:00.000Z";
// THE SERVER-STAMPED CUSTODY INSTANT, deliberately recent and deliberately
// unlike the two above: a test measuring from the wrong one produces a visibly
// wrong answer.
const CUSTODY_AT = "2026-09-02T00:00:00.000Z";

const D = n => `sha256:${String(n).padStart(2, "0").repeat(32)}`;

const CONTEXT = Object.freeze({ actor: Object.freeze({ slug: "joe", human: true }) });

/** One artifact preimage, exactly as ops.f01_corporate_artifact hashes it. */
function artifactPreimage(overrides = {}) {
  return {
    schema_version: V5_F01_ARTIFACT_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    source_system: "outlook_test",
    source_class: "mailbox",
    source_account: "test-account@example.invalid",
    native_identity: {
      source_system: "outlook_test",
      native_id: "AAMkTESTITEM0001",
      native_id_epoch: "epoch-test-1",
    },
    native_version: "4",
    content_digest: D(11),
    byte_length: 4096,
    observed_at: SOURCE_OBSERVED,
    provenance: {
      adapter_kind: "graph_mail_test",
      evidence_ref: "test/mailbox/AAMkTESTITEM0001",
      retrieval_class: "connector_fetch",
    },
    evidence_class: "corporate_mailbox_item",
    declared_data_classes: ["lease_economics"],
    taint_class: "untrusted_external",
    ...overrides,
  };
}

/**
 * The envelope the reviewed store builds and the SQL CHECK constraints recompute,
 * built THROUGH that store's own helper so the fixture cannot drift from the
 * shape a real row carries.
 */
function storedEnvelope(record) {
  // The SAME three extras ops.f01_record_artifact is called with, so the fixture
  // is the envelope the writer actually stores rather than a near-miss.
  const envelope = v5F01StoreEnvelope("stored_corporate_artifact", record,
    { is_fact: false, makes_field_authoritative: false, immutable: true });
  return { envelope, envelope_digest: digest(envelope) };
}

/** What ops.f01_stored_artifact returns, field for field. */
function storedArtifactBody(record) {
  const { envelope, envelope_digest } = storedEnvelope(record);
  return {
    artifact_digest: digest(record),
    artifact: record,
    created_at: record.observed_at,
    source_observed_at: record.observed_at,
    // Custody. Nothing in the adapter is allowed to read it.
    recorded_at: CUSTODY_AT,
    envelope,
    envelope_digest,
    integrity: "recomputed_from_committed_row",
  };
}

const isPlainRecord = value =>
  value !== null && typeof value === "object" && !Array.isArray(value);

/** What ops.f01_derivative_coverage returns: 'unknown' for every artifact, by design. */
function coverageBody(artifact_digest, links = []) {
  return {
    artifact_digest,
    state: "unknown",
    reason_id: "producer_closure_not_established",
    // `e.link ->> 'derivative_kind'`, which yields SQL NULL for an element that is
    // not an object carrying that key rather than raising. The fixture has to
    // match that, or a malformed link dies HERE and the adapter never sees the
    // shape the case exists to hand it.
    registered_derivative_kinds:
      [...new Set(links.map(l => (isPlainRecord(l) ? l.derivative_kind ?? null : null)))].sort(),
    registered_links: links,
    registered_link_count: links.length,
    is_exhaustive_inventory: false,
    empty_link_set_means_verified_absence: false,
    integrity: "recomputed_from_committed_rows",
  };
}

/**
 * THE EXACT TEN KEYS ops.f01_derivative_links projects, and no eleventh.
 *
 * `source_artifact_digest` is deliberately ABSENT: the SQL filters on that column
 * and never returns it, so a link carries no field naming the artifact it belongs
 * to. The artifact is stated once, at the coverage answer's own `artifact_digest`.
 * An earlier fixture invented the field, which made a per-link check look green
 * against a shape the database cannot produce.
 */
function link(overrides = {}) {
  return {
    link_digest: D(31),
    derivative_kind: "f01_parsed_proposal",
    derivative_id: "proposal-test-1",
    derivative_content_digest: D(32),
    producer_workflow: "test_parser",
    producer_run_ref: "run-test-1",
    produced_at: CUSTODY_AT,
    evidence_ref: "test/run/1",
    evidence_digest: D(33),
    registered_by: "joe",
    ...overrides,
  };
}

const MAIL = artifactPreimage();
const MAIL_DIGEST = digest(MAIL);
const DOC = artifactPreimage({
  source_system: "onedrive_test",
  source_class: "drive",
  native_identity: {
    source_system: "onedrive_test", native_id: "01TESTDOC", native_id_epoch: "epoch-test-1",
  },
  native_version: "2",
  content_digest: D(12),
  evidence_class: "corporate_document_bytes",
  provenance: {
    adapter_kind: "graph_drive_test",
    evidence_ref: "test/drive/01TESTDOC",
    retrieval_class: "connector_fetch",
  },
});
const DOC_DIGEST = digest(DOC);

// --- the scripted handle ---------------------------------------------------

/**
 * A fake database handle, NOT a fake store: the store under `createContextAssembly
 * Source` below is the reviewed one, so every statement the adapter causes is the
 * statement the real store sends.
 */
class FakeDb {
  constructor(script = {}) {
    this.script = { artifacts: {}, coverage: {}, ...script };
    this.calls = [];
    this.began = 0;
    this.committed = 0;
    this.rolledBack = 0;
  }

  async query(text, params = []) {
    this.calls.push({ text, params });
    if (text === "BEGIN") { this.began += 1; return { rows: [] }; }
    if (text === "COMMIT") { this.committed += 1; return { rows: [] }; }
    if (text === "ROLLBACK") { this.rolledBack += 1; return { rows: [] }; }

    if (text.includes("ops.f01_principal()") && text.includes("ops.f01_now_text()")) {
      return { rows: [{
        principal: this.script.principal ?? {
          actor_slug: "joe", human: true, authorization_class: "verified_partner",
          derived_by: "server_established_transaction_context",
        },
        server_now: this.script.server_now ?? SERVER_NOW,
      }] };
    }
    if (text.includes("ops.f01_read(")) {
      const kind = params[0];
      const selector = JSON.parse(params[1] ?? "null") ?? {};
      return { rows: [{ body: this.readBody(kind, selector) }] };
    }
    throw new Error(`unscripted statement: ${text}`);
  }

  readBody(kind, selector) {
    const inner = kind === "artifact"
      ? this.script.artifacts[selector.artifact_digest] ?? null
      : kind === "derivative_coverage"
        ? this.script.coverage[selector.artifact_digest]
          ?? coverageBody(selector.artifact_digest)
        : null;
    return {
      operation: "read-record-source-authority",
      kind,
      tenant: this.script.body_tenant ?? ORGANIZATION_TENANT_ID,
      actor_slug: this.script.body_actor_slug ?? "joe",
      server_time: this.script.server_now ?? SERVER_NOW,
      policy_digest: null,
      body: inner,
      integrity: "recomputed_not_trusted",
      external_effects: false,
    };
  }

  /** Every f01_read the adapter caused, as (kind, selector) pairs. */
  reads() {
    return this.calls
      .filter(call => call.text.includes("ops.f01_read("))
      .map(call => ({ kind: call.params[0], selector: JSON.parse(call.params[1]) }));
  }

  statements() {
    return this.calls.map(call => (["BEGIN", "COMMIT", "ROLLBACK"].includes(call.text)
      ? call.text
      : call.text.includes("ops.f01_read(") ? `ops.f01_read(${call.params[0]})`
        : call.text.includes("ops.f01_principal()") ? "ops.f01_principal+f01_now_text"
          : call.text));
  }
}

function sourceWith(script = {}) {
  const db = new FakeDb({
    artifacts: {
      [MAIL_DIGEST]: storedArtifactBody(MAIL),
      [DOC_DIGEST]: storedArtifactBody(DOC),
    },
    ...script,
  });
  const store = createRecordSourceAuthorityStore({ db });
  return { db, store, source: createContextAssemblySource({ store }) };
}

const select = (...digests) =>
  ({ selection: digests.map(artifact_digest => ({ kind: "artifact", artifact_digest })) });

// --- the caller's half of the assembly, which stays the caller's ------------

const facts = (overrides = {}) => ({
  action: "document.send",
  actor_class: "verified_partner",
  audience: "client",
  environment: "production",
  lifecycle_transition: "send",
  resource_class: "document",
  risk_tier: "consequential",
  ...overrides,
});

const universePolicy = (overrides = {}) => ({
  schema_version: V5_F05_UNIVERSE_SCHEMA_VERSION,
  universe_version: 1,
  tenant: ORGANIZATION_TENANT_ID,
  completeness: "partial_unknown_coverage",
  declared_actions: ["document.send"],
  declared_resource_classes: ["deal", "document"],
  rules: [
    {
      rule_id: "deal-tone",
      version: 1, rule_class: "scoped_judgment", scope: "shared", owner: "joe",
      mandatory: false,
      // Deliberately not applicable to the task below, so this fixture is about
      // the RECORDS and not about a rule universe this adapter does not read.
      trigger: { resource_class: ["deal"] },
      binding_text: "Write to a client the way Joe would: plain, specific, no hedging.",
      no_machine_control_reason: "Only a reader applying context can tell plain from curt.",
      retirement: { behavior: "permanent_until_superseded" },
    },
  ],
  ...overrides,
});

const template = (overrides = {}) => ({
  task: {
    task_id: "t-source-1",
    title: "Explore the stored mailbox item",
    boundary_action: "business.send_client_document",
    facts: facts(),
  },
  controls: {
    deal_owner_slug: "joe",
    account_slug: "joe",
    policy_scope: ["business.send_client_document"],
    capabilities: ["document.send"],
  },
  universe: compileRuleUniverse(universePolicy()),
  ...overrides,
});

async function project(script, request = select(MAIL_DIGEST), context = CONTEXT) {
  const { db, source } = sourceWith(script);
  return { db, projection: await source.readSourceProjection(request, context) };
}

function refuses(fn, code) {
  try {
    fn();
  } catch (error) {
    assert.ok(error instanceof V5F05SourceError || error.name === "V5F05Error",
      `expected a typed refusal, got ${error?.name}: ${error?.message}`);
    assert.equal(error.code, code);
    return error;
  }
  assert.fail(`expected a refusal with code ${code}`);
}

async function rejects(promise, code) {
  await assert.rejects(promise, error => {
    assert.ok(error instanceof V5F05SourceError || error.name === "V5F05Error",
      `expected a typed refusal, got ${error?.name}: ${error?.message}`);
    assert.equal(error.code, code);
    return true;
  });
}

// -------------------------------------- the mapping that must work

test("one stored artifact projects into one F05 record F05 itself accepts", async () => {
  const { db, projection } = await project();

  assert.equal(projection.schema_version, V5_F05_SOURCE_PROJECTION_SCHEMA_VERSION);
  assert.equal(projection.decision, "allow");
  assert.equal(projection.reason_id, "source_records_projected");
  assert.equal(projection.assembly_permitted, true);
  assert.deepEqual(projection.unmapped, []);
  assert.equal(projection.tenant, ORGANIZATION_TENANT_ID);
  assert.deepEqual(projection.effects, V5_NO_EFFECTS);

  assert.equal(projection.records.length, 1);
  const record = projection.records[0];
  assert.equal(record.record_id, MAIL_DIGEST);
  assert.equal(record.record_kind, "message");
  assert.equal(record.origin, "email");
  assert.equal(record.version, 4);
  assert.equal(record.content_digest, MAIL.content_digest);
  // NOT primary: F01 cannot establish that an artifact is derived from nothing.
  assert.equal(record.derived_kind, V5_F05_UNKNOWN_UPSTREAM_DERIVED_KIND);
  assert.deepEqual(record.derived_from, []);
  assert.equal(record.estimated_tokens, 0);
  assert.equal(record.max_age_seconds, null);
  assert.equal(record.provenance.source_id, V5_F05_SOURCE_STORED_COPY_SOURCE_ID);
  assert.equal(record.provenance.retrieval_class, V5_F05_SOURCE_RETRIEVAL_CLASS);
  assert.equal(record.provenance.evidence_ref, MAIL.provenance.evidence_ref);

  // The queries and sources are DERIVED, and each one names the read it came from.
  assert.deepEqual(projection.queries.map(q => q.query_kind),
    ["f01_read.artifact", "f01_read.derivative_coverage"]);
  assert.equal(projection.queries.find(q => q.query_kind === "f01_read.artifact").query_id,
    record.query_id);
  assert.equal(projection.queries[0].parameters_digest,
    digest({ kind: "artifact", artifact_digest: MAIL_DIGEST }));
  assert.deepEqual(projection.sources.map(s => s.source_id),
    [V5_F05_SOURCE_STORED_COPY_SOURCE_ID]);
  assert.equal(projection.sources[0].state, "available");
  assert.equal(projection.sources[0].note, V5_F05_SOURCE_STORED_COPY_NOTE);

  // The assembler accepts it, unchanged, through the real freeze/assemble path.
  const assembled = assembleContextFromSource({ projection, template: template() });
  assert.equal(assembled.decision, "allow");
  assert.equal(verifyContextManifest(assembled.manifest), true);
  assert.deepEqual(assembled.manifest.records.map(r => r.record_id), [MAIL_DIGEST]);
  const projected = assembled.manifest.records[0];
  assert.equal(projected.origin, "email");
  assert.equal(projected.taint_class, "untrusted_external");
  assert.equal(projected.tainted, true);
  assert.equal(projected.may_instruct, false);
  assert.equal(assembled.manifest.mode, V5_F05_SOURCE_MODE);
  assert.equal(db.rolledBack, 0);
});

test("the unknown upstream survives into the manifest and blocks its own write", async () => {
  const { projection } = await project();
  const assembled = assembleContextFromSource({ projection, template: template() });
  const manifest = assembled.manifest;

  // THE NESTED MANIFEST, not the wrapper around it. A false flag on the outer
  // answer is worth nothing if the manifest inside says the write may proceed.
  assert.equal(manifest.consequential_action_permitted, false);
  assert.equal(manifest[manifest.write_gate_field], false);
  assert.ok(manifest.blocking_reasons.includes("record_upstream_lineage_unknown"));
  assert.deepEqual(manifest.unknown_lineage_records, [MAIL_DIGEST]);
  assert.equal(manifest.uncertainty.marker, true);
  assert.equal(manifest.uncertainty.unknown_lineage_record_count, 1);
  assert.equal(manifest.records[0].upstream_lineage_known, false);
  assert.equal(manifest.records[0].derived_kind, V5_F05_UNKNOWN_UPSTREAM_DERIVED_KIND);
  assert.equal(manifest.taint_lineage[0].upstream_lineage_known, false);

  // Qualified exploration still runs, which is the whole point of not refusing.
  assert.equal(manifest.decision, "allow");
  assert.equal(manifest.reason_id, "read_only_exploration_under_uncertainty");
  assert.equal(manifest.read_only_exploration_permitted, true);

  // And the projection says the same thing about itself, before assembly.
  assert.equal(projection.lineage.record_derived_kind,
    V5_F05_UNKNOWN_UPSTREAM_DERIVED_KIND);
  assert.equal(projection.lineage.upstream_lineage_established, false);
  assert.equal(projection.lineage.primary_lineage_claimed, false);
});

test("two selected artifacts both land, and both cite the one seat that answered", async () => {
  const { projection } = await project({}, select(MAIL_DIGEST, DOC_DIGEST));
  assert.deepEqual(projection.records.map(r => r.record_kind).sort(), ["document", "message"]);
  assert.deepEqual(projection.records.map(r => r.origin).sort(), ["document", "email"]);
  // ONE source entry, whatever the two artifacts' external systems are: F01's
  // stored copy is the only thing this adapter reached.
  assert.deepEqual(projection.sources.map(s => s.source_id),
    [V5_F05_SOURCE_STORED_COPY_SOURCE_ID]);
  assert.ok(projection.records.every(
    r => r.provenance.source_id === V5_F05_SOURCE_STORED_COPY_SOURCE_ID));
  assert.equal(projection.queries.length, 4);
  const assembled = assembleContextFromSource({ projection, template: template() });
  assert.deepEqual(assembled.manifest.records.map(r => r.record_id).sort(),
    [DOC_DIGEST, MAIL_DIGEST].sort());
  // Sorted on a COPY: the manifest's own array is frozen, and this assertion
  // must not be the one thing that mutates it.
  assert.deepEqual([...assembled.manifest.unknown_lineage_records].sort(),
    [DOC_DIGEST, MAIL_DIGEST].sort());
  assert.ok(Object.isFrozen(assembled.manifest.unknown_lineage_records));
  assert.equal(assembled.manifest.consequential_action_permitted, false);
});

// -------------------------------------- the clock, and the two instants

test("the record's observed_at is the source's own old instant, never custody or now", async () => {
  const { projection } = await project();
  const record = projection.records[0];
  assert.equal(record.observed_at, SOURCE_OBSERVED);
  assert.notEqual(record.observed_at, CUSTODY_AT);
  assert.notEqual(record.observed_at, SERVER_NOW);
  assert.equal(projection.uncertainty.custody_recorded_at_used_as_observed_at, false);
  assert.equal(projection.uncertainty.observed_at_source, "artifact_preimage_observed_at");

  // And the manifest reports the real age rather than a refreshed one.
  const assembled = assembleContextFromSource({ projection, template: template() });
  const projected = assembled.manifest.records[0];
  assert.equal(projected.observed_at, SOURCE_OBSERVED);
  assert.equal(projected.age_seconds,
    (Date.parse(SERVER_NOW) - Date.parse(SOURCE_OBSERVED)) / 1000);
  assert.ok(projected.age_seconds > 3 * 365 * 24 * 3600 - 86400);
  // No caller freshness policy exists, so none is invented in either direction.
  assert.equal(projected.max_age_seconds, null);
  assert.equal(projection.uncertainty.freshness_verdict_invented, false);
});

test("`now` is the server instant F01 returned, and no caller field can set it", async () => {
  const { projection } = await project({ server_now: "2026-09-09T13:45:00.000Z" });
  assert.equal(projection.now, "2026-09-09T13:45:00.000Z");
  const assembled = assembleContextFromSource({ projection, template: template() });
  assert.equal(assembled.manifest.now, "2026-09-09T13:45:00.000Z");
  refuses(() => assembleContextFromSource({
    projection, template: template({ now: "2026-09-09T23:59:00.000Z" }),
  }), "caller_derived_field_refused");
});

// -------------------------------------- the read surface

test("the adapter reaches the database only through the registered read path", async () => {
  const { db } = await project();
  assert.deepEqual(db.statements(), [
    "BEGIN", "ops.f01_principal+f01_now_text", "ops.f01_read(artifact)", "COMMIT",
    "BEGIN", "ops.f01_principal+f01_now_text", "ops.f01_read(derivative_coverage)", "COMMIT",
  ]);
  assert.equal(db.began, 2);
  assert.equal(db.committed, 2);
  assert.equal(db.rolledBack, 0);
  // Not one writer, not one unregistered function, not one direct DML statement.
  const text = db.calls.map(call => call.text).join("\n");
  for (const forbidden of ["INSERT", "UPDATE", "DELETE", "f01_record_", "f01_install_policy",
    "f01_apply_observation", "f01_register_derivative_link", "f01_document_version_source",
    "f01_stored_artifact(", "f01_replay_outcome"]) {
    assert.ok(!text.includes(forbidden), `statement stream must not contain ${forbidden}`);
  }
});

test("a document selector is refused without issuing any read", async () => {
  const { db, projection } = await project({}, {
    selection: [{ kind: "document", document_id: "doc-test-1" }],
  });
  assert.equal(projection.decision, "refuse");
  assert.equal(projection.reason_id,
    "document_provenance_not_readable_through_registered_read_kinds");
  assert.equal(projection.assembly_permitted, false);
  assert.deepEqual(projection.records, []);
  assert.deepEqual(db.reads(), []);
  assert.deepEqual(db.calls, []);

  const assembled = assembleContextFromSource({ projection, template: template() });
  assert.equal(assembled.decision, "refuse");
  assert.equal(assembled.reason_id, "source_projection_incomplete");
  assert.equal(assembled.manifest, null);
  assert.deepEqual(assembled.unmapped_reason_ids,
    ["document_provenance_not_readable_through_registered_read_kinds"]);
});

test("a registered read kind that carries no F05 record is named and not read", async () => {
  for (const [kind, reason_id] of [
    ["document_versions", "document_provenance_not_readable_through_registered_read_kinds"],
    ["derivative_links", "derivative_record_not_readable_through_registered_read_kinds"],
    ["holds", "read_kind_not_mappable_to_f05_record"],
    ["current_policy", "read_kind_not_mappable_to_f05_record"],
  ]) {
    const { db, projection } = await project({}, { selection: [{ kind }] });
    assert.equal(projection.unmapped[0].reason_id, reason_id, kind);
    assert.deepEqual(projection.records, [], kind);
    assert.deepEqual(db.calls, [], kind);
  }
});

test("an evidence reference the assembler would refuse is unmapped, not truncated", async () => {
  // NOT A LEGAL STORED ROW, and the earlier note here was wrong about why. Both
  // bounds are 255 — F01's assertProvenance and the F05 guard — so F01 could never
  // have admitted this artifact in the first place. The case is kept as a defence
  // against a row this module did not write and cannot re-validate end to end: if
  // an over-long reference ever arrives, it is reported as unmapped rather than
  // trimmed to fit, and it blocks the assembly like any other unmapped selector.
  const record = artifactPreimage({
    provenance: {
      adapter_kind: "graph_mail_test",
      evidence_ref: `test/${"x".repeat(300)}`,
      retrieval_class: "connector_fetch",
    },
  });
  const artifact_digest = digest(record);
  const { projection } = await project(
    { artifacts: { [artifact_digest]: storedArtifactBody(record) } }, select(artifact_digest));
  assert.equal(projection.unmapped[0].reason_id, "f05_record_guard_refused");
  assert.equal(projection.unmapped[0].detail.code, "text_too_long");
  assert.deepEqual(projection.records, []);
});

test("an unregistered read kind refuses rather than being attempted", async () => {
  const { source } = sourceWith();
  await rejects(source.readSourceProjection(
    { selection: [{ kind: "rule_universe" }] }, CONTEXT), "unknown_read_kind");
});

// -------------------------------------- what must not become a record

test("the three ambiguous evidence classes are unmapped, never labelled", async () => {
  const ambiguous = V5_F01_EVIDENCE_CLASSES.filter(
    cls => !Object.prototype.hasOwnProperty.call(V5_F05_SOURCE_EVIDENCE_CLASS_MAP, cls));
  assert.deepEqual(ambiguous.sort(),
    ["corporate_field_snapshot", "corporate_record_export", "corporate_report_render"]);

  for (const evidence_class of ambiguous) {
    const record = artifactPreimage({ evidence_class });
    const artifact_digest = digest(record);
    const { projection } = await project(
      { artifacts: { [artifact_digest]: storedArtifactBody(record) } },
      select(artifact_digest));
    assert.equal(projection.decision, "refuse", evidence_class);
    assert.deepEqual(projection.records, []);
    assert.equal(projection.unmapped[0].reason_id, "origin_not_derivable_from_evidence_class");
    assert.equal(projection.unmapped[0].artifact_digest, artifact_digest);
    assert.equal(assembleContextFromSource({ projection, template: template() }).manifest, null);
  }
});

test("a native_version that is not a canonical decimal is unmapped, and none is invented",
  async () => {
    for (const native_version of ["v2", "02", "2.0", "2024-05-01", "  3", "3 ", "٣", "0",
      "1e3", "+4", "-4", "9007199254740993"]) {
      const record = artifactPreimage({ native_version });
      const artifact_digest = digest(record);
      const { projection } = await project(
        { artifacts: { [artifact_digest]: storedArtifactBody(record) } },
        select(artifact_digest));
      assert.equal(projection.decision, "refuse", native_version);
      assert.deepEqual(projection.records, [], native_version);
      assert.equal(projection.unmapped[0].reason_id,
        "artifact_native_version_not_canonical_decimal", native_version);
      assert.equal(assembleContextFromSource({ projection, template: template() }).manifest,
        null, native_version);
    }
  });

test("a canonical decimal maps to the integer it renders as, losslessly", async () => {
  for (const native_version of ["1", "4", "37", "1000000"]) {
    const record = artifactPreimage({ native_version });
    const artifact_digest = digest(record);
    const { projection } = await project(
      { artifacts: { [artifact_digest]: storedArtifactBody(record) } },
      select(artifact_digest));
    assert.equal(projection.records[0].version, Number(native_version));
    assert.equal(String(projection.records[0].version), native_version);
  }
  // And the projection says what that mapping is and is not.
  const { projection } = await project();
  assert.equal(projection.uncertainty.version_mapping,
    "artifact_native_version_canonical_decimal");
  assert.equal(projection.uncertainty.version_is_source_ordering_claim, false);
});

test("an artifact the store does not hold is named, not skipped", async () => {
  const missing = D(77);
  const { projection } = await project({}, select(MAIL_DIGEST, missing));
  assert.equal(projection.decision, "refuse");
  assert.equal(projection.reason_id, "artifact_not_stored");
  assert.deepEqual(projection.unmapped.map(u => u.artifact_digest), [missing]);
  // The one that DID map is still visible — and still cannot be assembled.
  assert.deepEqual(projection.records.map(r => r.record_id), [MAIL_DIGEST]);
  assert.equal(projection.assembly_permitted, false);
  assert.equal(assembleContextFromSource({ projection, template: template() }).manifest, null);
});

test("an artifact observed after the read instant is unmapped", async () => {
  const record = artifactPreimage({ observed_at: "2026-09-09T18:00:00.000Z" });
  const artifact_digest = digest(record);
  const { projection } = await project(
    { artifacts: { [artifact_digest]: storedArtifactBody(record) } }, select(artifact_digest));
  assert.equal(projection.unmapped[0].reason_id, "artifact_observed_after_read_instant");
  assert.deepEqual(projection.records, []);
});

test("every unmapped reason the module can report is in its closed list", async () => {
  const seen = new Set();
  const cases = [
    [artifactPreimage({ evidence_class: "corporate_report_render" }), null],
    [artifactPreimage({ native_version: "v9" }), null],
    [artifactPreimage({ observed_at: "not-an-instant" }), null],
    [artifactPreimage({ provenance: { adapter_kind: "a", retrieval_class: "b" } }), null],
    [artifactPreimage({ content_digest: "not-a-digest" }), null],
    [artifactPreimage({ source_system: "not a usable ident" }), null],
  ];
  for (const [record] of cases) {
    const artifact_digest = digest(record);
    const { projection } = await project(
      { artifacts: { [artifact_digest]: storedArtifactBody(record) } }, select(artifact_digest));
    assert.equal(projection.records.length, 0, canonicalJson(record.evidence_class));
    seen.add(projection.unmapped[0].reason_id);
  }
  for (const reason of seen) {
    assert.ok(V5_F05_SOURCE_UNMAPPED_REASONS.includes(reason), reason);
  }
  assert.deepEqual([...seen].sort(), [
    "artifact_content_digest_unreadable",
    "artifact_native_version_not_canonical_decimal",
    "artifact_observed_at_unreadable",
    "artifact_provenance_incomplete",
    "artifact_source_system_not_a_usable_source_id",
    "origin_not_derivable_from_evidence_class",
  ]);
});

// -------------------------------------- tamper, tenant and identity
//
// NONE OF THE ROWS BELOW IS A LEGAL STORED ROW, and that is the point of them.
// ops.f01_corporate_artifact's CHECK constraints recompute both digests and bind
// the observed-instant column to the hashed preimage, so a committed row cannot
// be in any of these states. What they model is an answer that was corrupted or
// substituted BETWEEN the database and this module — the one thing F01's own
// recomputation cannot cover — and the adapter's response is to refuse rather
// than to repair, summarise or partially consume it.

test("a tampered preimage under an unchanged digest refuses; no manifest is emitted",
  async () => {
    const body = storedArtifactBody(MAIL);
    // The bytes are edited and the digests are left alone, which is exactly what a
    // row whose CHECK constraints were bypassed would look like in transit.
    const tampered = {
      ...body,
      artifact: { ...body.artifact, taint_class: "first_party_record_layer" },
    };
    const { source } = sourceWith({ artifacts: { [MAIL_DIGEST]: tampered } });
    await rejects(source.readSourceProjection(select(MAIL_DIGEST), CONTEXT),
      "stored_artifact_record_digest_mismatch");
  });

test("an envelope whose record is not the artifact returned beside it refuses", async () => {
  const body = storedArtifactBody(MAIL);
  const other = artifactPreimage({ native_version: "9" });
  const swapped = {
    ...body,
    envelope: { ...body.envelope, record: other },
  };
  swapped.envelope_digest = digest(swapped.envelope);
  const { source } = sourceWith({ artifacts: { [MAIL_DIGEST]: swapped } });
  await rejects(source.readSourceProjection(select(MAIL_DIGEST), CONTEXT),
    "stored_artifact_envelope_record_mismatch");
});

test("an envelope whose own digest was left behind refuses", async () => {
  const body = storedArtifactBody(MAIL);
  const stale = { ...body, envelope_digest: D(88) };
  const { source } = sourceWith({ artifacts: { [MAIL_DIGEST]: stale } });
  await rejects(source.readSourceProjection(select(MAIL_DIGEST), CONTEXT),
    "stored_artifact_envelope_digest_mismatch");
});

test("a row whose observed instant disagrees with its own hashed preimage refuses", async () => {
  const body = storedArtifactBody(MAIL);
  const drifted = { ...body, source_observed_at: CUSTODY_AT, created_at: CUSTODY_AT };
  const { source } = sourceWith({ artifacts: { [MAIL_DIGEST]: drifted } });
  await rejects(source.readSourceProjection(select(MAIL_DIGEST), CONTEXT),
    "stored_artifact_observed_at_incoherent");
});

test("an answer about another artifact refuses rather than being adopted", async () => {
  const { source } = sourceWith({ artifacts: { [MAIL_DIGEST]: storedArtifactBody(DOC) } });
  await rejects(source.readSourceProjection(select(MAIL_DIGEST), CONTEXT),
    "stored_artifact_identity_mismatch");
});

test("a read bound to another tenant refuses and emits nothing", async () => {
  const { source } = sourceWith({ body_tenant: "some-other-tenant" });
  await rejects(source.readSourceProjection(select(MAIL_DIGEST), CONTEXT),
    "read_tenant_mismatch");
});

test("a read whose database actor is not the store's refuses", async () => {
  const { source } = sourceWith({ body_actor_slug: "dell" });
  await rejects(source.readSourceProjection(select(MAIL_DIGEST), CONTEXT),
    "actor_context_mismatch");
});

test("the same selector twice refuses before any statement is sent", async () => {
  const { db, source } = sourceWith();
  await rejects(source.readSourceProjection(select(MAIL_DIGEST, MAIL_DIGEST), CONTEXT),
    "duplicate_selector");
  assert.deepEqual(db.calls, []);
});

test("a selector field the kind does not read is refused, so no duplicate slips past",
  async () => {
    // The bypass this closes: an ignored `document_id` still reached the dedupe
    // key, so these two selectors hashed differently, both read the same artifact,
    // and both produced a record at the same record_id — the duplicate arriving at
    // the kernel instead of at this module's own guard.
    const { db, source } = sourceWith();
    await rejects(source.readSourceProjection({
      selection: [
        { kind: "artifact", artifact_digest: MAIL_DIGEST },
        { kind: "artifact", artifact_digest: MAIL_DIGEST, document_id: "doc-test-1" },
      ],
    }, CONTEXT), "irrelevant_selector_field");
    assert.deepEqual(db.calls, []);

    // Both directions, and a kind that reads neither field.
    await rejects(source.readSourceProjection({
      selection: [{ kind: "document", document_id: "d", artifact_digest: MAIL_DIGEST }],
    }, CONTEXT), "irrelevant_selector_field");
    await rejects(source.readSourceProjection({
      selection: [{ kind: "holds", artifact_digest: MAIL_DIGEST }],
    }, CONTEXT), "irrelevant_selector_field");

    // The ordinary selectors still work, and still dedupe on what they do read.
    const { projection } = await project({}, select(MAIL_DIGEST));
    assert.equal(projection.records.length, 1);
  });

// -------------------------------------- lineage that stays unknown

test("an artifact with a real registered link still reaches F05 as one record",
  async () => {
    // THE SHAPE THE DATABASE ACTUALLY PRODUCES: ten keys, no source digest on the
    // link. An artifact with a registered derivative is the ordinary case — a
    // parsed proposal registers one in the same transaction as the proposal — so
    // this is the path that must not refuse.
    const { projection } = await project({
      coverage: { [MAIL_DIGEST]: coverageBody(MAIL_DIGEST, [link()]) },
    });
    assert.deepEqual(Object.keys(link()).sort(),
      [...V5_F05_SOURCE_DERIVATIVE_LINK_KEYS].sort());
    assert.ok(!Object.prototype.hasOwnProperty.call(link(), "source_artifact_digest"));

    assert.equal(projection.decision, "allow");
    assert.equal(projection.assembly_permitted, true);
    assert.deepEqual(projection.records.map(r => r.record_id), [MAIL_DIGEST]);

    // And it assembles, through the real kernel, with the link reported and
    // mapped into nothing.
    const manifest = assembleContextFromSource({ projection, template: template() }).manifest;
    assert.deepEqual(manifest.records.map(r => r.record_id), [MAIL_DIGEST]);
    assert.equal(manifest.records[0].derived_kind, V5_F05_UNKNOWN_UPSTREAM_DERIVED_KIND);
    assert.deepEqual(manifest.records[0].derived_from, []);
    assert.equal(manifest.consequential_action_permitted, false);
  });

test("registered links are observations, never records, and coverage stays unknown",
  async () => {
    const { projection } = await project({
      coverage: { [MAIL_DIGEST]: coverageBody(MAIL_DIGEST, [link()]) },
    });
    assert.equal(projection.records.length, 1);
    const observed = projection.observations[0];
    assert.equal(observed.derivative_coverage_state, "unknown");
    assert.equal(observed.derivative_coverage_reason_id, "producer_closure_not_established");
    assert.equal(observed.registered_link_count, 1);
    assert.deepEqual(observed.registered_derivative_kinds, ["f01_parsed_proposal"]);
    assert.equal(observed.derivatives_mapped_into_records, 0);
    assert.equal(observed.lineage_complete, false);
    assert.equal(observed.empty_link_set_means_verified_absence, false);

    // The projection's own lineage block says what silence does not mean.
    assert.deepEqual(projection.lineage.derivative_coverage_states, ["unknown"]);
    assert.equal(projection.lineage.lineage_complete, false);
    assert.equal(projection.lineage.absent_link_means_no_derivative, false);
    assert.equal(projection.lineage.absent_link_means_first_party_origin, false);
    assert.equal(projection.lineage.upstream_derivation_inside_source_system_unknown, true);
    assert.equal(projection.lineage.derivative_records_mapped, 0);
  });

test("an empty link set is not read as verified absence", async () => {
  const { projection } = await project();
  assert.equal(projection.observations[0].registered_link_count, 0);
  assert.equal(projection.observations[0].derivative_coverage_state, "unknown");
  assert.equal(projection.observations[0].lineage_complete, false);
});

test("the coverage answer, not a per-link field, binds links to the artifact read",
  async () => {
    // The binding F01 offers is the answer-level one: ops.f01_derivative_links
    // filters WHERE source_artifact_digest = p_artifact_digest and projects no
    // such key, and ops.f01_derivative_coverage names the artifact once. A
    // coverage answer about a different artifact is therefore the refusal that
    // matters, and it is checked before anything is read off the link list.
    const { source } = sourceWith({
      coverage: { [MAIL_DIGEST]: coverageBody(D(66), [link()]) },
    });
    await rejects(source.readSourceProjection(select(MAIL_DIGEST), CONTEXT),
      "derivative_coverage_identity_mismatch");
  });

test("a link that is not the shape f01_derivative_links emits is refused", async () => {
  // NOT A LEGAL STORED ROW. ops.f01_derivative_links builds every link with the
  // same ten keys and its columns are NOT NULL, so none of the cases below can
  // come from a committed row; they are what a corrupted or substituted answer in
  // transit looks like, and the adapter refuses rather than summarising it.
  const { link_digest: _dropped, ...missingKey } = link();
  // The fixture must hand these to the ADAPTER intact. `->>` yields NULL for an
  // element that is not an object, so the malformed link survives construction
  // and the refusal below is the subject's, not the fixture's.
  const built = coverageBody(MAIL_DIGEST, [null]);
  assert.deepEqual(built.registered_links, [null]);
  assert.deepEqual(built.registered_derivative_kinds, [null]);
  assert.equal(built.registered_link_count, 1);

  for (const malformed of [missingKey, "not-an-object", null]) {
    const { source } = sourceWith({
      coverage: { [MAIL_DIGEST]: coverageBody(MAIL_DIGEST, [malformed]) },
    });
    await assert.rejects(source.readSourceProjection(select(MAIL_DIGEST), CONTEXT),
      error => {
        assert.ok(error instanceof V5F05SourceError, `${error?.name}: ${error?.message}`);
        assert.ok(["derivative_link_shape_unrecognized", "derivative_link_unreadable"]
          .includes(error.code), error.code);
        return true;
      });
  }
});

test("a coverage answer claiming an exhaustive inventory is refused, not consumed", async () => {
  for (const claim of [{ is_exhaustive_inventory: true },
    { empty_link_set_means_verified_absence: true }]) {
    const { source } = sourceWith({
      coverage: { [MAIL_DIGEST]: { ...coverageBody(MAIL_DIGEST), ...claim } },
    });
    await rejects(source.readSourceProjection(select(MAIL_DIGEST), CONTEXT),
      "derivative_coverage_claims_completeness");
  }
});

test("a coverage state other than the one F01 produces is refused, not carried", async () => {
  assert.equal(V5_F05_SOURCE_CONSUMABLE_COVERAGE_STATE, "unknown");
  // `established` is a state nothing in this slice can reach, and a row asserting
  // it — even alongside its own `is_exhaustive_inventory: false`, which is the
  // CONTRADICTORY shape — is a completeness claim this module cannot check.
  // Reading past the contradiction to whichever field suits is the failure.
  for (const state of ["established", "partial", "", null, 7, "UNKNOWN"]) {
    const { source } = sourceWith({
      coverage: { [MAIL_DIGEST]: { ...coverageBody(MAIL_DIGEST), state } },
    });
    await rejects(source.readSourceProjection(select(MAIL_DIGEST), CONTEXT),
      "derivative_coverage_state_not_consumable");
  }

  // The state F01 does produce is carried through verbatim and never upgraded.
  const { projection } = await project();
  assert.equal(projection.observations[0].derivative_coverage_state, "unknown");
  assert.deepEqual(projection.lineage.derivative_coverage_states, ["unknown"]);
  assert.equal(projection.lineage.lineage_complete, false);
});

test("the source that answered is F01's stored copy, not the corporate system", async () => {
  const { projection } = await project({}, select(MAIL_DIGEST, DOC_DIGEST));

  // The seat that answered is named as the source, and it says what it is.
  assert.deepEqual(projection.sources.map(s => s.source_id),
    [V5_F05_SOURCE_STORED_COPY_SOURCE_ID]);
  assert.ok(projection.sources[0].note.includes("was not contacted"));
  // The external systems are NOT source ids: nothing here reached outlook_test or
  // onedrive_test, and a stale stored copy would answer exactly the same way.
  const sourceIds = projection.sources.map(s => s.source_id);
  for (const external of ["outlook_test", "onedrive_test"]) {
    assert.ok(!sourceIds.includes(external), external);
    assert.ok(!projection.records.some(r => r.provenance.source_id === external), external);
  }

  // They are kept as EVIDENCE instead, on the observation and in the record's own
  // evidence reference.
  assert.deepEqual(projection.observations.map(o => o.source_system).sort(),
    ["onedrive_test", "outlook_test"]);
  assert.ok(projection.observations.every(o => o.external_source_contacted === false));
  assert.ok(projection.observations.every(o => o.external_source_liveness_checked === false));
  assert.ok(projection.observations.every(
    o => o.answered_from === V5_F05_SOURCE_STORED_COPY_SOURCE_ID));
  assert.deepEqual(projection.uncertainty.external_source_systems,
    ["onedrive_test", "outlook_test"]);
  assert.equal(projection.uncertainty.external_source_contacted, false);
  assert.equal(projection.uncertainty.external_source_liveness_checked, false);
  assert.equal(projection.uncertainty.answered_by, V5_F05_SOURCE_STORED_COPY_SOURCE_ID);
  assert.ok(projection.records.some(
    r => r.provenance.evidence_ref === MAIL.provenance.evidence_ref));

  // The manifest carries the same one source, and no claim about a live system.
  const manifest = assembleContextFromSource({ projection, template: template() }).manifest;
  assert.deepEqual(manifest.sources.map(s => s.source_id),
    [V5_F05_SOURCE_STORED_COPY_SOURCE_ID]);
  assert.deepEqual(manifest.unavailable_sources, []);
  assert.deepEqual(manifest.conflicting_sources, []);
});

test("an unknown-lineage record cannot be edited into a clean or authoritative one",
  async () => {
    const { projection } = await project();
    const [mapped] = projection.records;

    // Every laundering attempt, run through the REAL kernel on the real projected
    // record. Each is refused by name rather than reaching a manifest.
    const attempts = [
      [{ origin: "record_layer" }, "unknown_lineage_requires_external_origin"],
      [{ record_kind: "rule" }, "unknown_lineage_cannot_bear_authority"],
      [{ record_kind: "decision" }, "unknown_lineage_cannot_bear_authority"],
      [{ record_kind: "authority_grant" }, "unknown_lineage_cannot_bear_authority"],
      [{ record_kind: "summary" }, "derived_kind_inconsistent_with_record_kind"],
      [{ record_kind: "embedding" }, "derived_kind_inconsistent_with_record_kind"],
      [{ derived_kind: "summary" }, "derived_record_without_lineage"],
      [{ derived_from: ["r-ghost"] }, "taint_lineage_dangling_parent"],
    ];
    for (const [override, expected] of attempts) {
      const forged = { ...mapped, ...override };
      try {
        compileTaintLineage([forged]);
        assert.fail(`expected ${expected} for ${canonicalJson(override)}`);
      } catch (error) {
        assert.ok(error instanceof V5F05Error, `${error?.name}: ${error?.message}`);
        assert.equal(error.code, expected, canonicalJson(override));
      }
    }

    // And the same edit cannot travel through the adapter either: the projection
    // no longer hashes to its own digest.
    refuses(() => assembleContextFromSource({
      projection: { ...projection, records: [{ ...mapped, origin: "record_layer" }] },
      template: template(),
    }), "source_projection_digest_mismatch");

    // A caller cannot assert the flag directly either — it is not an input field,
    // so a request claiming its own lineage status is refused as unknown.
    refuses(() => compileTaintLineage([{ ...mapped, upstream_lineage_known: true }]),
      "unknown_field");
  });

test("no mapped record can carry a first-party origin", async () => {
  for (const mapping of Object.values(V5_F05_SOURCE_EVIDENCE_CLASS_MAP)) {
    assert.ok(V5_F05_EXTERNAL_ORIGINS.includes(mapping.origin), mapping.origin);
  }
  const { projection } = await project({}, select(MAIL_DIGEST, DOC_DIGEST));
  assert.ok(projection.records.every(r => V5_F05_EXTERNAL_ORIGINS.includes(r.origin)));
  assert.ok(projection.records.every(r => r.origin !== "record_layer"));
});

test("F01 calling an artifact first-party does not lower the taint F05 computes", async () => {
  const record = artifactPreimage({ taint_class: "first_party_record_layer" });
  const artifact_digest = digest(record);
  const { projection } = await project(
    { artifacts: { [artifact_digest]: storedArtifactBody(record) } }, select(artifact_digest));
  assert.equal(projection.observations[0].f01_taint_class, "first_party_record_layer");
  const assembled = assembleContextFromSource({ projection, template: template() });
  assert.equal(assembled.manifest.records[0].taint_class, "untrusted_external");
  assert.equal(assembled.manifest.records[0].tainted, true);
});

// -------------------------------------- reproducibility and mutation

test("the same store answers produce byte-identical bytes twice", async () => {
  const first = await project();
  const second = await project();
  assert.equal(canonicalJson(first.projection), canonicalJson(second.projection));
  assert.equal(first.projection.source_projection_digest,
    second.projection.source_projection_digest);

  const a = assembleContextFromSource({ projection: first.projection, template: template() });
  const b = assembleContextFromSource({ projection: second.projection, template: template() });
  assert.equal(a.input_bytes, b.input_bytes);
  assert.equal(a.input_digest, b.input_digest);
  assert.equal(a.manifest_digest, b.manifest_digest);

  // And the bytes re-derive the same manifest for anyone holding them, which is
  // the whole content of "reproducible proposal".
  const rederived = assembleContextManifest({
    frozen: true,
    schema_version: V5_F05_FROZEN_INPUT_SCHEMA_VERSION,
    input_bytes: a.input_bytes,
    input_digest: a.input_digest,
  });
  assert.equal(rederived.manifest_digest, a.manifest_digest);
});

test("a projection edited after it was read cannot be assembled", async () => {
  const { projection } = await project();
  const forged = {
    ...projection,
    records: projection.records.map(r => ({ ...r, origin: "record_layer" })),
  };
  refuses(() => assembleContextFromSource({ projection: forged, template: template() }),
    "source_projection_digest_mismatch");

  const flipped = { ...projection, unmapped: [{ reason_id: "artifact_not_stored" }] };
  refuses(() => assembleContextFromSource({ projection: flipped, template: template() }),
    "source_projection_digest_mismatch");
});

test("a projection carrying an accessor is refused before it is hashed", async () => {
  const { projection } = await project();
  // The digest compare must not read one value and the assembly another. This is
  // a shape refusal, not an authentication: the digest is unkeyed and the module
  // says so, but the two reads at least see the same bytes.
  const live = { ...projection };
  let reads = 0;
  Object.defineProperty(live, "now", {
    enumerable: true, configurable: true,
    get() { reads += 1; return reads === 1 ? projection.now : "2030-01-01T00:00:00.000Z"; },
  });
  refuses(() => assembleContextFromSource({ projection: live, template: template() }),
    "accessor_property_refused");

  // And a non-enumerable own field is refused rather than silently dropped.
  const hidden = { ...projection };
  Object.defineProperty(hidden, "assembly_permitted", { value: true, enumerable: false });
  refuses(() => assembleContextFromSource({ projection: hidden, template: template() }),
    "non_enumerable_key_refused");
});

test("a hand-built object is not a projection", async () => {
  refuses(() => assembleContextFromSource({
    projection: { assembly_permitted: true, records: [], unmapped: [] }, template: template(),
  }), "projection_not_compiled");
});

test("the projection is frozen and a caller mutating its own template changes nothing",
  async () => {
    const { projection } = await project();
    assert.ok(Object.isFrozen(projection));
    assert.ok(Object.isFrozen(projection.records));
    assert.ok(Object.isFrozen(projection.records[0]));
    assert.throws(() => { projection.records[0].version = 99; }, TypeError);
    assert.throws(() => { projection.assembly_permitted = false; }, TypeError);

    const live = template();
    const assembled = assembleContextFromSource({ projection, template: live });
    live.task.task_id = "t-mutated";
    live.task.facts.risk_tier = "routine";
    const again = assembleContextFromSource({ projection, template: template() });
    assert.equal(assembled.input_bytes, again.input_bytes);
    assert.equal(assembled.manifest_digest, again.manifest_digest);
    assert.equal(assembled.manifest.task.task_id, "t-source-1");
  });

// -------------------------------------- what a caller may not reach

test("no template key can add, replace or rename a record, query or source", async () => {
  const { projection } = await project();
  const forgedRecord = {
    record_id: "r-attacker", record_kind: "rule", version: 1, content_digest: D(9),
    origin: "record_layer", derived_kind: "primary", derived_from: [], query_id: "q-x",
    observed_at: SOURCE_OBSERVED, estimated_tokens: 1,
    provenance: { source_id: "src-x", retrieval_class: "typed_read" },
  };
  const attempts = {
    records: [forgedRecord],
    queries: [{ query_id: "q-x", query_kind: "k", parameters_digest: D(9),
      retrieved_at: SERVER_NOW }],
    sources: [{ source_id: "src-x", state: "available" }],
    tenant: "some-other-tenant",
    now: "2026-01-01T00:00:00.000Z",
    mode: "consequential_action_proposal",
    actor: { slug: "joe", human: true },
    budget: { token_budget: 10 },
    schema_version: V5_F05_MANIFEST_SCHEMA_VERSION,
  };
  for (const [key, value] of Object.entries(attempts)) {
    assert.ok(V5_F05_SOURCE_REFUSED_TEMPLATE_KEYS.includes(key), key);
    refuses(() => assembleContextFromSource({
      projection, template: template({ [key]: value }),
    }), "caller_derived_field_refused");
  }

  // An unknown key is refused too, so nothing rides along unread.
  refuses(() => assembleContextFromSource({
    projection, template: template({ semantic_addition: [] }),
  }), "unknown_field");

  // And the manifest carries exactly the store's record, at the store's id.
  const assembled = assembleContextFromSource({ projection, template: template() });
  assert.deepEqual(assembled.manifest.records.map(r => r.record_id), [MAIL_DIGEST]);
  assert.equal(assembled.manifest.tenant, ORGANIZATION_TENANT_ID);
});

test("the assembled mode is exploration and no answer claims a consequential action",
  async () => {
    const { projection } = await project();
    const assembled = assembleContextFromSource({ projection, template: template() });
    assert.equal(assembled.mode, "read_only_exploration");
    assert.equal(assembled.manifest.mode, "read_only_exploration");
    assert.equal(assembled.consequential_action_supported_by_this_projection, false);
    assert.equal(assembled.consequential_execution_permitted, false);
    assert.equal(projection.uncertainty.consequential_action_supported_by_this_projection,
      false);
    // F05's own answer is carried verbatim beside ours rather than overwritten,
    // and the manifest itself marks the exploration as uncertain.
    assert.equal(assembled.manifest_consequential_action_permitted,
      assembled.manifest.consequential_action_permitted);
    assert.equal(assembled.manifest.uncertainty.marker, true);
    assert.equal(assembled.manifest.reason_id, "read_only_exploration_under_uncertainty");
    assert.ok(assembled.manifest.blocking_reasons.includes("universe_coverage_unknown"));
  });

test("every answer is an unauthenticated reproducible proposal with no trust anchor",
  async () => {
    const { projection } = await project();
    assert.equal(projection.projection_kind, "reproducible_proposal");
    assert.equal(projection.authenticated, false);
    assert.equal(projection.trust_anchor, null);
    assert.equal(projection.records_written, 0);
    assert.equal(projection.provider_calls, 0);

    for (const answer of [
      assembleContextFromSource({ projection, template: template() }),
      assembleContextFromSource({
        projection: (await project({}, {
          selection: [{ kind: "document", document_id: "doc-test-1" }],
        })).projection,
        template: template(),
      }),
    ]) {
      assert.equal(answer.projection_kind, "reproducible_proposal");
      assert.equal(answer.authenticated, false);
      assert.equal(answer.trust_anchor, null);
      assert.equal(answer.attestation_minted, false);
      assert.equal(answer.verifier_registered, false);
      assert.equal(answer.consequential_action_supported_by_this_projection, false);
      assert.deepEqual(answer.effects, V5_NO_EFFECTS);
    }
    assert.equal(assembleContextFromSource({ projection, template: template() })
      .manifest.projection_kind, "reproducible_proposal");
  });

// -------------------------------------- construction and contract

test("the adapter is built over trusted code, never over a handle or a credential", () => {
  refuses(() => createContextAssemblySource(), "store_required");
  refuses(() => createContextAssemblySource({ store: {} }), "store_required");
  refuses(() => createContextAssemblySource({ store: { query: async () => ({ rows: [] }) } }),
    "store_required");
  refuses(() => createContextAssemblySource({
    store: { schema_version: "other", readRecordSourceAuthority: async () => null },
  }), "store_schema_mismatch");

  const { source } = sourceWith();
  assert.equal(source.schema_version, V5_F05_SOURCE_SCHEMA_VERSION);
  assert.deepEqual(Object.keys(source).sort(),
    ["assembleContextFromSource", "readSourceProjection", "schema_version"]);
  assert.ok(Object.isFrozen(source));
});

test("a selection is bounded and non-empty", async () => {
  const { source } = sourceWith();
  await rejects(source.readSourceProjection({ selection: [] }, CONTEXT), "invalid_selection");
  await rejects(source.readSourceProjection({
    selection: Array.from({ length: V5_F05_SOURCE_MAX_SELECTION + 1 },
      (_, i) => ({ kind: "artifact", artifact_digest: D(i % 90) })),
  }, CONTEXT), "invalid_selection");
  await rejects(source.readSourceProjection({ selection: [{ kind: "artifact" }] }, CONTEXT),
    "missing_field");
  await rejects(source.readSourceProjection({ selection: [{ kind: "artifact",
    artifact_digest: "not-a-digest" }] }, CONTEXT), "invalid_digest");
});

test("the contract names what this adapter does not do, and hashes it", () => {
  const preimage = v5F05SourceContractPreimage();
  assert.equal(preimage.schema_version, V5_F05_SOURCE_SCHEMA_VERSION);
  assert.equal(preimage.store_schema_version, V5_F01_STORE_SCHEMA_VERSION);
  assert.equal(preimage.manifest_schema_version, V5_F05_MANIFEST_SCHEMA_VERSION);
  for (const key of ["writes_records", "issues_sql_of_its_own", "opens_connection",
    "registers_tool",
    "accepts_caller_clock", "accepts_caller_records", "accepts_caller_queries",
    "accepts_caller_sources", "accepts_caller_tenant", "accepts_caller_verifier",
    "mints_attestation", "emits_authenticated_projection", "emits_first_party_origin",
    "lowers_taint", "establishes_derivative_coverage", "claims_complete_lineage",
    "substitutes_custody_instant_for_observation", "refreshes_observed_at_on_import",
    "invents_record_version", "invents_estimated_tokens", "invents_freshness_window",
    "reads_unregistered_read_kind", "consequential_action_supported",
    "claims_primary_lineage", "claims_external_source_answered",
    "consumes_established_coverage_state"]) {
    assert.equal(preimage[key], false, key);
  }
  assert.equal(preimage.record_derived_kind, V5_F05_UNKNOWN_UPSTREAM_DERIVED_KIND);
  assert.equal(preimage.stored_copy_source_id, V5_F05_SOURCE_STORED_COPY_SOURCE_ID);
  assert.equal(preimage.consumable_coverage_state, V5_F05_SOURCE_CONSUMABLE_COVERAGE_STATE);
  // The link projection this module reads, and the binding it relies on.
  assert.deepEqual(preimage.derivative_link_keys, [...V5_F05_SOURCE_DERIVATIVE_LINK_KEYS]);
  assert.equal(preimage.derivative_link_carries_source_artifact_digest, false);
  assert.equal(preimage.derivative_links_bound_by, "coverage_answer_artifact_digest");
  assert.equal(preimage.artifact_and_coverage_read_atomically, false);
  // And the manifest version this adapter builds against is the one it states.
  assert.equal(preimage.manifest_schema_version, "doctorcre-v5-f05-context-manifest.v2");
  assert.equal(preimage.unmapped_selector_blocks_assembly, true);
  assert.equal(preimage.reads_only_through_registered_store_read, true);
  assert.equal(v5F05SourceContractDigest(), digest(preimage));
  assert.equal(v5F05SourceContractCanonicalBytes(), canonicalJson(preimage));
});

test("the open seams are stated, and none of them claims to have landed", () => {
  const gaps = contextAssemblySourceGaps();
  assert.deepEqual(gaps.map(g => g.gap).sort(), [
    "no_derivative_record_reader",
    "no_document_provenance_read_kind",
    "no_manifest_persistence",
    "no_registered_verifier",
    "no_rule_universe_reader",
    "no_runtime_read_verification",
  ]);
  assert.ok(gaps.every(gap => gap.landed === false));
  assert.ok(Object.isFrozen(gaps));
});
