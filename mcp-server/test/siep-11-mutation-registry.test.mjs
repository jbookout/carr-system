import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  assertCurrentSourceInventoryMatchesFixture,
  assertGeneratedFrontierMatchesCommitted,
  assertLegacyLaunchdSource,
  DB_CATALOG_BASELINE,
  discoverScriptEntrypoints,
  frozenInventory,
  fullInventory,
  HISTORICAL_REGISTRY_ARTIFACT_SHA256,
  HISTORICAL_REGISTRY_SEALS,
  isScriptEntrypoint,
  jobDefinitionInventory,
  JOB_DEFINITION_BASELINE,
  mcpInventory,
  parsePlistXml,
  parseGitIndexEntries,
  registryDigest,
  REGISTRY_V7_VERSION,
  REGISTRY_V8_VERSION,
  REGISTRY_V9_VERSION,
  REGISTRY_V10_VERSION,
  REGISTRY_V11_VERSION,
  REGISTRY_V12_VERSION,
  REGISTRY_V13_VERSION,
  REGISTRY_V14_VERSION,
  REGISTRY_V15_VERSION,
  REGISTRY_V16_VERSION,
  REGISTRY_V17_VERSION,
  REGISTRY_V18_VERSION,
  REGISTRY_V19_VERSION,
  REGISTRY_V20_VERSION,
  replaceExactlyOnce,
  renderGeneratedFrontier,
  renderRuntimeProjection,
  renderSIEP16IntegratedRegistrySql,
  renderSIEP17ForwardRegistrySql,
  renderSIEP18ForwardRegistrySql,
  renderCodexContinuityForwardRegistrySql,
  renderClaudeContinuityForwardRegistrySql,
  renderClaudeStartupForwardRegistrySql,
  renderClaudeActorHydrationForwardRegistrySql,
  renderClaudeConfigPreservationForwardRegistrySql,
  renderCodexCompactionCheckpointForwardRegistrySql,
  renderBackupGuardStatusForwardRegistrySql,
  renderSourcedShapeForwardCorrectionDomainSql,
  renderSourcedShapeForwardCorrectionRegistrySql,
  renderIncidentWorkRequestLinkDomainSql,
  renderIncidentWorkRequestLinkRegistrySql,
  renderContinuityArchiveForwardRegistrySql,
  assertContinuityArchiveV20TrustRoot,
  renderSourceMergeForwardRegistrySql,
  sha256,
  SIEP16_INTEGRATED_DB_CATALOG_BASELINE,
  SIEP17_FORWARD_DB_CATALOG_BASELINE,
  SIEP18_FORWARD_DB_CATALOG_BASELINE,
  SIEP18_MONITOR_ARTIFACT_SHA256,
  SIEP18_PRE_V9_DB_CATALOG_BASELINE,
  CODEX_CONTINUITY_FORWARD_DB_CATALOG_BASELINE,
  CODEX_CONTINUITY_PRE_V11_DB_CATALOG_BASELINE,
  CLAUDE_CONTINUITY_FORWARD_DB_CATALOG_BASELINE,
  CLAUDE_CONTINUITY_PRE_V12_DB_CATALOG_BASELINE,
  CLAUDE_STARTUP_FORWARD_DB_CATALOG_BASELINE,
  CLAUDE_STARTUP_PRE_V13_DB_CATALOG_BASELINE,
  CLAUDE_ACTOR_HYDRATION_FORWARD_DB_CATALOG_BASELINE,
  CLAUDE_ACTOR_HYDRATION_PRE_V14_DB_CATALOG_BASELINE,
  CLAUDE_CONFIG_PRESERVATION_FORWARD_DB_CATALOG_BASELINE,
  CLAUDE_CONFIG_PRESERVATION_PRE_V15_DB_CATALOG_BASELINE,
  CODEX_COMPACTION_CHECKPOINT_FORWARD_DB_CATALOG_BASELINE,
  CODEX_COMPACTION_CHECKPOINT_PRE_V16_DB_CATALOG_BASELINE,
  BACKUP_GUARD_STATUS_FORWARD_DB_CATALOG_BASELINE,
  BACKUP_GUARD_STATUS_PRE_V17_DB_CATALOG_BASELINE,
  SOURCED_SHAPE_FORWARD_CORRECTION_FORWARD_DB_CATALOG_BASELINE,
  SOURCED_SHAPE_FORWARD_CORRECTION_PRE_V18_DB_CATALOG_BASELINE,
  SOURCED_SHAPE_FORWARD_CORRECTION_WITNESS_SHA256,
  INCIDENT_WORK_REQUEST_LINK_FORWARD_DB_CATALOG_BASELINE,
  INCIDENT_WORK_REQUEST_LINK_PRE_V19_DB_CATALOG_BASELINE,
  CONTINUITY_ARCHIVE_FORWARD_DB_CATALOG_BASELINE,
  CONTINUITY_ARCHIVE_PRE_V20_DB_CATALOG_BASELINE,
  SOURCE_MERGE_FORWARD_DB_CATALOG_BASELINE,
  SOURCE_MERGE_PRE_V10_DB_CATALOG_BASELINE,
  SIEP12_DB_CATALOG_BASELINE,
  validateLaunchdAuthorityCatalogs,
  workflowDefinitionInventory,
} from "../../ops/scac-mutation-inventory.mjs";
import {
  assertClosedTopLevel,
  assertRegisteredOperation,
  MutationRegistryRefusal,
  registeredOperation,
  SCAC_MUTATION_REGISTRY_DIGEST,
  SCAC_MUTATION_REGISTRY_VERSION,
} from "../src/mutation-registry.js";
import { TOOLS } from "../src/tools.js";

const migration = fs.readFileSync(
  new URL("../../migrations/0454_siep11_mutation_registry.sql", import.meta.url), "utf8");
const generated = fs.readFileSync(
  new URL("../src/scac-mutation-registry.generated.js", import.meta.url), "utf8");
const generatedV2 = fs.readFileSync(
  new URL("../src/scac-mutation-registry.v2.generated.js", import.meta.url), "utf8");
const successorMigration = fs.readFileSync(
  new URL("../../migrations/0455_siep12_policy_epoch.sql", import.meta.url), "utf8");
const generatedV3 = fs.readFileSync(
  new URL("../src/scac-mutation-registry.v3.generated.js", import.meta.url), "utf8");
const v3Migration = fs.readFileSync(
  new URL("../../migrations/0457_siep13_forward_mutation_registry.sql", import.meta.url), "utf8");
const generatedV4 = fs.readFileSync(
  new URL("../src/scac-mutation-registry.v4.generated.js", import.meta.url), "utf8");
const v4Migration = fs.readFileSync(
  new URL("../../migrations/0459_siep14_forward_mutation_registry.sql", import.meta.url), "utf8");
const generatedV5 = fs.readFileSync(
  new URL("../src/scac-mutation-registry.v5.generated.js", import.meta.url), "utf8");
const v5Migration = fs.readFileSync(
  new URL("../../migrations/0461_siep15_forward_mutation_registry.sql", import.meta.url), "utf8");
const generatedV6 = fs.readFileSync(
  new URL("../src/scac-mutation-registry.v6.generated.js", import.meta.url), "utf8");
const v6Migration = fs.readFileSync(
  new URL("../../migrations/0462_siep16_forward_mutation_registry.sql", import.meta.url), "utf8");
const generatedV7 = fs.readFileSync(
  new URL("../src/scac-mutation-registry.v7.generated.js", import.meta.url), "utf8");
const v7Migration = fs.readFileSync(
  new URL("../../migrations/0464_siep16_integrated_mutation_registry.sql", import.meta.url), "utf8");
const generatedV8 = fs.readFileSync(
  new URL("../src/scac-mutation-registry.v8.generated.js", import.meta.url), "utf8");
const v8Migration = fs.readFileSync(
  new URL("../../migrations/0466_siep17_forward_mutation_registry.sql", import.meta.url), "utf8");
const generatedV9 = fs.readFileSync(
  new URL("../src/scac-mutation-registry.v9.generated.js", import.meta.url), "utf8");
const v9Migration = fs.readFileSync(
  new URL("../../migrations/0468_siep18_forward_mutation_registry.sql", import.meta.url), "utf8");
const generatedV10 = fs.readFileSync(
  new URL("../src/scac-mutation-registry.v10.generated.js", import.meta.url), "utf8");
const v10Migration = fs.readFileSync(
  new URL("../../migrations/0471_source_merge_catalog_registry_successor.sql", import.meta.url), "utf8");
const generatedV11 = fs.readFileSync(
  new URL("../src/scac-mutation-registry.v11.generated.js", import.meta.url), "utf8");
const v11Migration = fs.readFileSync(
  new URL("../../migrations/0481_codex_continuity_registry_activation.sql", import.meta.url), "utf8");
const generatedV12 = fs.readFileSync(
  new URL("../src/scac-mutation-registry.v12.generated.js", import.meta.url), "utf8");
const v12Migration = fs.readFileSync(
  new URL("../../migrations/0486_claude_continuity_registry_activation.sql", import.meta.url), "utf8");
const generatedV13 = fs.readFileSync(
  new URL("../src/scac-mutation-registry.v13.generated.js", import.meta.url), "utf8");
const v13Migration = fs.readFileSync(
  new URL("../../migrations/0487_claude_startup_registry_activation.sql", import.meta.url), "utf8");
const generatedV14 = fs.readFileSync(
  new URL("../src/scac-mutation-registry.v14.generated.js", import.meta.url), "utf8");
const v14Migration = fs.readFileSync(
  new URL("../../migrations/0488_claude_actor_hydration_registry_activation.sql", import.meta.url), "utf8");
const generatedV15 = fs.readFileSync(
  new URL("../src/scac-mutation-registry.v15.generated.js", import.meta.url), "utf8");
const v15Migration = fs.readFileSync(
  new URL("../../migrations/0489_claude_config_preservation_registry_activation.sql", import.meta.url), "utf8");
const generatedV16 = fs.readFileSync(
  new URL("../src/scac-mutation-registry.v16.generated.js", import.meta.url), "utf8");
const v16Migration = fs.readFileSync(
  new URL("../../migrations/0490_codex_compaction_checkpoint_registry_activation.sql", import.meta.url), "utf8");
const generatedV17 = fs.readFileSync(
  new URL("../src/scac-mutation-registry.v17.generated.js", import.meta.url), "utf8");
const v17Migration = fs.readFileSync(
  new URL("../../migrations/0491_backup_guard_status_registry_activation.sql", import.meta.url), "utf8");
const generatedV18 = fs.readFileSync(
  new URL("../src/scac-mutation-registry.v18.generated.js", import.meta.url), "utf8");
const v18Migration = fs.readFileSync(
  new URL("../../migrations/0492_sourced_shape_forward_correction_and_scac_successor.sql", import.meta.url), "utf8");
const generatedV19 = fs.readFileSync(
  new URL("../src/scac-mutation-registry.v19.generated.js", import.meta.url), "utf8");
const v19Migration = fs.readFileSync(
  new URL("../../migrations/0493_incident_work_request_link_scac_successor.sql", import.meta.url), "utf8");
const generatedV20 = fs.readFileSync(
  new URL("../src/scac-mutation-registry.v20.generated.js", import.meta.url), "utf8");
const v20Migration = fs.readFileSync(
  new URL("../../migrations/0494_codex_continuity_archive_registry.sql", import.meta.url), "utf8");
const siep18MonitorMigration = fs.readFileSync(
  new URL("../../migrations/0467_siep18_atomic_db_monitor_grants.sql", import.meta.url), "utf8");
const directRegistryRedefinitions = [
  "0460_siep15_device_enrollment.sql",
  "0465_siep17_token_challenge_authority.sql",
  "0467_siep18_atomic_db_monitor_grants.sql",
  "0470_source_merge_authority_projection.sql",
].map(name => [name, fs.readFileSync(new URL(`../../migrations/${name}`, import.meta.url), "utf8")]);

test("successor generation refuses absent or ambiguous predecessor markers", () => {
  assert.equal(replaceExactlyOnce("before marker after", "marker", "successor", "unit"),
    "before successor after");
  assert.throws(() => replaceExactlyOnce("before after", "marker", "successor", "unit"),
    /unit marker count must be exactly one/);
  assert.throws(() => replaceExactlyOnce("marker and marker", "marker", "successor", "unit"),
    /unit marker count must be exactly one/);
});

test("reviewed MCP inventory is an exact immutable projection of the assembled registry", () => {
  const rows = mcpInventory(TOOLS);
  assert.equal(rows.length, 228);
  assert.equal(rows.filter(row => row.write).length, 160);
  assert.equal(rows.filter(row => !row.write).length, 68);
  assert.deepEqual(rows.map(row => row.operation), Object.keys(TOOLS).sort());
  assert.equal(Object.isFrozen(TOOLS), true);
  assert.equal(Object.isFrozen(TOOLS["add-loop"]), true);
  assert.equal(Object.isFrozen(TOOLS["add-loop"].inputSchema), true);
  assert.throws(() => { TOOLS["add-loop"].handler = async () => ({ ok: true }); }, TypeError);
  assert.equal(new Set(rows.map(row => row.ingress_key)).size, rows.length);
  assert.equal(new Set(rows.map(row => row.schema_digest)).has(undefined), false);
  assert.equal(rows.find(row => row.operation === "append-tour-rights-receipt").source_locator,
    "mcp-server/src/tour-rights-projection.js");
  assert.equal(rows.find(row => row.operation === "record-tour-map-promotion-receipt").source_locator,
    "mcp-server/src/tour-map-promotion.js");
  assert.equal(rows.find(row => row.operation === "request-tour-pdf-render").source_locator,
    "mcp-server/src/tour-artifacts.js");
  const governanceQueue = rows.find(row => row.operation === "governance-queue");
  assert.ok(governanceQueue);
  assert.equal(governanceQueue.write, false);
  assert.equal(governanceQueue.human_only, false);
  assert.equal(governanceQueue.authority_only, false);
  assert.equal(governanceQueue.effect_class, "audit_side_effect");
  assert.equal(governanceQueue.classification_authorizing, false);
});

test("sealed v1-v8 stay immutable historical evidence after the v9 successor", () => {
  const { v1: v1Seal, v2: v2Seal, v3: v3Seal, v4: v4Seal, v5: v5Seal,
    v6: v6Seal } = HISTORICAL_REGISTRY_SEALS;
  const historicalArtifacts = new Map([
    ["migrations/0454_siep11_mutation_registry.sql", migration],
    ["migrations/0455_siep12_policy_epoch.sql", successorMigration],
    ["migrations/0457_siep13_forward_mutation_registry.sql", v3Migration],
    ["migrations/0459_siep14_forward_mutation_registry.sql", v4Migration],
    ["mcp-server/src/scac-mutation-registry.generated.js", generated],
    ["mcp-server/src/scac-mutation-registry.v2.generated.js", generatedV2],
    ["mcp-server/src/scac-mutation-registry.v3.generated.js", generatedV3],
    ["mcp-server/src/scac-mutation-registry.v4.generated.js", generatedV4],
    ["migrations/0461_siep15_forward_mutation_registry.sql", v5Migration],
    ["mcp-server/src/scac-mutation-registry.v5.generated.js", generatedV5],
    ["migrations/0462_siep16_forward_mutation_registry.sql", v6Migration],
    ["mcp-server/src/scac-mutation-registry.v6.generated.js", generatedV6],
    ["migrations/0464_siep16_integrated_mutation_registry.sql", v7Migration],
    ["mcp-server/src/scac-mutation-registry.v7.generated.js", generatedV7],
    ["migrations/0466_siep17_forward_mutation_registry.sql", v8Migration],
    ["mcp-server/src/scac-mutation-registry.v8.generated.js", generatedV8],
  ]);
  for (const [path, contents] of historicalArtifacts)
    assert.equal(sha256(contents), HISTORICAL_REGISTRY_ARTIFACT_SHA256[path], `${path} changed after seal`);
  assert.match(generated, /SCAC_MUTATION_REGISTRY_VERSION = "scac-mutation-registry\.v1"/);
  assert.match(v7Migration,
    new RegExp(`'${HISTORICAL_REGISTRY_SEALS.v7.digest}',${HISTORICAL_REGISTRY_SEALS.v7.entryCount},${HISTORICAL_REGISTRY_SEALS.v7.sourceEntryCount},`));
  assert.match(v7Migration,
    new RegExp(`registry_version='scac-mutation-registry\\.v7'\\)<>${HISTORICAL_REGISTRY_SEALS.v7.entryCount}`));
  assert.match(v7Migration,
    new RegExp(`if observed_count<>${SIEP16_INTEGRATED_DB_CATALOG_BASELINE.secdef_execute.count} or observed_digest<>'${SIEP16_INTEGRATED_DB_CATALOG_BASELINE.secdef_execute.digest}' then return false`));
  assert.match(successorMigration, /scac-mutation-registry\.v1/);
  assert.match(successorMigration, /scac-mutation-registry\.v2/);
  assert.match(v3Migration, /scac-mutation-registry\.v3/);
  assert.match(v4Migration, /scac-mutation-registry\.v4/);
  assert.match(v5Migration, /scac-mutation-registry\.v5/);
  assert.match(v6Migration, /scac-mutation-registry\.v6/);
  assert.match(v7Migration, /scac-mutation-registry\.v7/);
  assert.match(v7Migration,
    /alter function ops\.scac_mutation_catalog_v6_current\(\) rename to scac_mutation_catalog_v6_live_at_seal/);
  assert.match(v7Migration, /create or replace function ops\.scac_mutation_registry_seal_valid\(p_registry_version text\)/);
  assert.match(v7Migration, /ops\.scac_mutation_registry_v6_seal_available\(\)/);
  assert.match(v7Migration, /source:=ops\.scac_policy_epoch_snapshot_v3\(\)/);
  assert.doesNotMatch(v7Migration, /source:=ops\.scac_policy_epoch_snapshot_v6\(\)/);
  for (const seal of [v1Seal, v2Seal, v3Seal, v4Seal, v5Seal, v6Seal])
    assert.match(v7Migration, new RegExp(`\\('${seal.version.replaceAll(".", "\\.")}','${seal.digest}',${seal.entryCount},${seal.sourceEntryCount}\\)`));
});

test("sealed v8 predecessor remains exact after source inventory advances", () => {
  const { v1: v1Seal, v2: v2Seal, v3: v3Seal, v4: v4Seal, v5: v5Seal,
    v6: v6Seal, v7: v7Seal, v8: v8Seal } = HISTORICAL_REGISTRY_SEALS;
  assert.equal(sha256(v8Migration),
    HISTORICAL_REGISTRY_ARTIFACT_SHA256["migrations/0466_siep17_forward_mutation_registry.sql"]);
  assert.equal(sha256(generatedV8),
    HISTORICAL_REGISTRY_ARTIFACT_SHA256["mcp-server/src/scac-mutation-registry.v8.generated.js"]);
  assert.equal(JSON.parse(generatedV8.match(/SCAC_MUTATION_REGISTRY_DIGEST = ("[0-9a-f]{64}")/)[1]),
    v8Seal.digest.slice("sha256:".length));
  assert.match(v8Migration,
    new RegExp(`'${v8Seal.digest}',${v8Seal.entryCount},${v8Seal.sourceEntryCount},`));
  assert.match(v8Migration, /scac-mutation-registry\.v8/);
  assert.match(v8Migration, /scac_mutation_catalog_v7_live_at_seal\(\)/);
  assert.match(v8Migration, /scac_mutation_registry_v7_seal_available\(\)/);
  assert.match(v8Migration, /scac_mutation_catalog_v8_current\(\)/);
  assert.match(v8Migration, new RegExp(`if observed_count<>${SIEP17_FORWARD_DB_CATALOG_BASELINE.secdef_execute.count} or observed_digest<>'${SIEP17_FORWARD_DB_CATALOG_BASELINE.secdef_execute.digest}' then return false`));
  for (const seal of [v1Seal, v2Seal, v3Seal, v4Seal, v5Seal, v6Seal, v7Seal])
    assert.match(v8Migration, new RegExp(`\\('${seal.version.replaceAll(".", "\\.")}','${seal.digest}',${seal.entryCount},${seal.sourceEntryCount}\\)`));
  assert.match(v8Migration, /,true,true,false,false,false,false,false\);/i);
});

test("v9 successor seals v8 and binds the measured SIEP-18 grant snapshot", () => {
  const seals = Object.values(HISTORICAL_REGISTRY_SEALS).filter(seal =>
    Number(seal.version.match(/[.]v(\d+)$/)[1]) < 9);
  const expectedEntryCount = HISTORICAL_REGISTRY_SEALS.v9.entryCount;
  assert.equal(sha256(generatedV9),
    HISTORICAL_REGISTRY_ARTIFACT_SHA256["mcp-server/src/scac-mutation-registry.v9.generated.js"]);
  assert.equal(sha256(v9Migration),
    HISTORICAL_REGISTRY_ARTIFACT_SHA256["migrations/0468_siep18_forward_mutation_registry.sql"]);
  assert.equal(HISTORICAL_REGISTRY_SEALS.v9.sourceEntryCount, 800);
  assert.equal(expectedEntryCount, 1439);
  assert.equal(sha256(siep18MonitorMigration), SIEP18_MONITOR_ARTIFACT_SHA256);
  assert.equal(SIEP18_PRE_V9_DB_CATALOG_BASELINE.secdef_execute.count, 338);
  assert.equal(SIEP18_FORWARD_DB_CATALOG_BASELINE.secdef_execute.count -
    SIEP18_PRE_V9_DB_CATALOG_BASELINE.secdef_execute.count, 4);
  assert.match(v9Migration,
    /observed_count<>338 or observed_digest<>'sha256:ccf023867a696884b2b9e50ae6eccc7b4e2afd9d7d6dbd1a93c01d8b1ec38555'/);
  assert.match(v9Migration, /Refuse before creating any v9 function/);
  for (const selfEffect of [
    /scac_mutation_registry_v8_seal_available\(\)/,
    /scac_mutation_registration_v9\(text,text\)/,
    /scac_mutation_catalog_v9_current\(\)/,
    /scac_policy_epoch_snapshot_v8\(\)/,
  ])
    assert.match(v9Migration, selfEffect);
  assert.match(v9Migration, new RegExp(`'${HISTORICAL_REGISTRY_SEALS.v9.digest}',${expectedEntryCount},800,`));
  assert.match(v9Migration, /scac_mutation_catalog_v8_live_at_seal\(\)/);
  assert.match(v9Migration, /scac_mutation_registry_v8_seal_available\(\)/);
  assert.match(v9Migration, /scac_mutation_catalog_v9_current\(\)/);
  assert.match(v9Migration, /registry\.registry_version='scac-mutation-registry\.v9'/);
  assert.match(v9Migration, /\(grant_snapshot->>'entry_count'\)::integer=297/);
  assert.match(v9Migration,
    /grant_snapshot->>'grant_digest'='sha256:0f04a50d8bc65e2dcc765b1981ab1d5091c809570f0a773db3f5c6e2b9d43501'/);
  assert.doesNotMatch(v9Migration, /measured_pending_v9_binding/);
  for (const seal of seals)
    assert.match(v9Migration, new RegExp(`\\('${seal.version.replaceAll(".", "\\.")}','${seal.digest}',${seal.entryCount},${seal.sourceEntryCount}\\)`));
});

test("v10 successor seals v9 and carries the generated source-merge control", () => {
  const rows = frozenInventory(REGISTRY_V10_VERSION);
  assert.equal(rows.length, 814);
  assert.equal(generatedV10, renderRuntimeProjection(rows, {
    version: REGISTRY_V10_VERSION, dbCatalogBaseline: SOURCE_MERGE_FORWARD_DB_CATALOG_BASELINE,
  }));
  assert.equal(JSON.parse(generatedV10.match(/SCAC_MUTATION_REGISTRY_DIGEST = ("[0-9a-f]{64}")/)[1]),
    HISTORICAL_REGISTRY_SEALS.v10.digest.slice("sha256:".length));
  assert.equal(sha256(generatedV10),
    HISTORICAL_REGISTRY_ARTIFACT_SHA256["mcp-server/src/scac-mutation-registry.v10.generated.js"]);
  assert.equal(sha256(v10Migration),
    HISTORICAL_REGISTRY_ARTIFACT_SHA256["migrations/0471_source_merge_catalog_registry_successor.sql"]);
  assert.equal(v10Migration, renderSourceMergeForwardRegistrySql(rows));
  assert.equal(SOURCE_MERGE_PRE_V10_DB_CATALOG_BASELINE.secdef_execute.count, 343);
  assert.equal(SOURCE_MERGE_FORWARD_DB_CATALOG_BASELINE.secdef_execute.count, 347);
  assert.match(v10Migration, /Refuse before creating any v10 function/);
  assert.match(v10Migration, /source_merge_eligibility/);
  assert.match(v10Migration, /GENERATED by ops\/sync_control_catalog\.py/);
  /* Was doesNotMatch until 2026-09-01. That encoded a snapshot of the state
     when the rule map had not moved since 0471's own preimage -- never a policy
     that 0471 must carry no repin. The generator emits one BY DESIGN when the
     map legitimately changes; reinstating canonical_edit (Joe's ruling
     7f48abf6, R02) moved the map digest, so the guarded repin is emitted.
     INVERTED, NOT DELETED: the migration stays constrained to the one verified,
     guarded behaviour. Verified read-only: production and staging each hold
     exactly 8 rule-delivery targets on the prior digest, so the guard passes
     and exactly 8 rows move. (Re-applied after the rebase onto R05's base.) */
  assert.match(v10Migration, /rule_map_repin/);
  assert.match(v10Migration, /expected eight exact rule-delivery target repins/);
  assert.match(v10Migration, /scac_mutation_registry_v9_seal_available\(\)/);
  assert.match(v10Migration, /scac_mutation_registration_v10\(text,text\)/);
  assert.match(v10Migration, /scac_mutation_catalog_v10_current\(\)/);
  assert.doesNotMatch(v10Migration,
    /alter function ops\.scac_mutation_catalog_v8_current\(\) rename to scac_mutation_catalog_v8_live_at_seal/);
  assert.match(v10Migration,
    /alter function ops\.scac_mutation_catalog_v9_current\(\) rename to scac_mutation_catalog_v9_live_at_seal/);
  assert.match(v10Migration, /registry\.registry_version='scac-mutation-registry\.v10'/);
  assert.match(v10Migration, /direct_database_grant_cutover',false/);
  assert.match(v10Migration, /production_enforcement_active',false/);
});

test("v11 seals the measured Codex continuity frontier", () => {
  const rows = frozenInventory(REGISTRY_V11_VERSION);
  assert.equal(rows.length, 819);
  assert.deepEqual(rows.filter(row => row.operation?.startsWith("codex-")).map(row => row.operation), [
    "codex-checkpoint", "codex-read-recovery", "codex-record-event",
  ]);
  assert.equal(generatedV11, renderRuntimeProjection(rows, {
    version: REGISTRY_V11_VERSION,
    dbCatalogBaseline: CODEX_CONTINUITY_FORWARD_DB_CATALOG_BASELINE,
  }));
  assert.equal(v11Migration, renderCodexContinuityForwardRegistrySql(rows));
  assert.equal(JSON.parse(generatedV11.match(/SCAC_MUTATION_REGISTRY_DIGEST = ("[0-9a-f]{64}")/)[1]),
    HISTORICAL_REGISTRY_SEALS.v11.digest.slice("sha256:".length));
  assert.match(generatedV11, /SCAC_MUTATION_DB_METADATA_AUTHORITY = true/);
  assert.equal(CODEX_CONTINUITY_PRE_V11_DB_CATALOG_BASELINE.secdef_execute.count, 347);
  assert.equal(CODEX_CONTINUITY_FORWARD_DB_CATALOG_BASELINE.secdef_execute.count, 351);
  assert.equal(CODEX_CONTINUITY_FORWARD_DB_CATALOG_BASELINE.secdef_execute.digest,
    "sha256:6bb739ea0422615f8150affcf24b83de0c2454ea485dc07d72f63bcda45a7014");
  assert.equal(CODEX_CONTINUITY_FORWARD_DB_CATALOG_BASELINE.runtime_dml_grants.count, 301);
  assert.match(v11Migration, /Refuse before creating any v11 function/);
  assert.match(v11Migration,
    /0480_codex_continuity[.]sql'[\s\S]+c1451a6c94b3be00f4099a83aa9519dee352fcc2cd0f198323696d2f42088aa4/);
  assert.match(v11Migration, /pre-v11 runtime grant receipt drifted/);
  assert.match(v11Migration,
    /array\['codex_continuity_checkpoint','codex_continuity_revision','codex_continuity_event'\]/);
  assert.match(v11Migration,
    /create trigger scac_reference_monitor_guard_row before insert or update or delete/);
  assert.match(v11Migration,
    /create trigger scac_reference_monitor_guard_truncate before truncate/);
  assert.match(v11Migration,
    /alter function ops[.]scac_mutation_catalog_v10_current[(][)] rename to scac_mutation_catalog_v10_live_at_seal/);
  assert.doesNotMatch(v11Migration,
    /alter function ops[.]scac_mutation_catalog_v9_current[(][)] rename to scac_mutation_catalog_v9_live_at_seal/);
  assert.match(v11Migration, /scac_mutation_registry_v10_seal_available[(][)]/);
  assert.match(v11Migration, /scac_mutation_registration_v11[(]text,text[)]/);
  assert.match(v11Migration, /scac_mutation_catalog_v11_current[(][)]/);
  assert.match(v11Migration, /scac_policy_epoch_snapshot_v10[(][)]/);
  assert.match(v11Migration, /registry[.]registry_version='scac-mutation-registry[.]v11'/);
  assert.match(v11Migration, /[(]grant_snapshot->>'entry_count'[)]::integer=301/);
  assert.match(v11Migration,
    /grant_snapshot->>'grant_digest'='sha256:dcf95363b3388bbb104e455a154fbe1da0a228f38df0c9317d8c191373706e73'/);
  assert.match(v11Migration,
    /registry_version='scac-mutation-registry[.]v11'[^\n]+<>1471/);
  for (const seal of Object.values(HISTORICAL_REGISTRY_SEALS)
    .filter(seal => Number(seal.version.split(".v")[1]) < 11))
    assert.match(v11Migration, new RegExp(`\\('${seal.version.replaceAll(".", "\\.")}','${seal.digest}',${seal.entryCount},${seal.sourceEntryCount}\\)`));
});

test("v12 seals the measured Claude continuity frontier", () => {
  const rows = frozenInventory(REGISTRY_V12_VERSION);
  assert.equal(rows.length, 825);
  assert.deepEqual(rows.filter(row => row.ingress_kind === "mcp_tool" && row.operation?.startsWith("claude-")).map(row => row.operation), [
    "claude-checkpoint", "claude-read-recovery", "claude-record-event",
  ]);
  assert.equal(generatedV12, renderRuntimeProjection(rows, {
    version: REGISTRY_V12_VERSION,
    dbCatalogBaseline: CLAUDE_CONTINUITY_FORWARD_DB_CATALOG_BASELINE,
  }));
  assert.equal(v12Migration, renderClaudeContinuityForwardRegistrySql(rows));
  assert.equal(JSON.parse(generatedV12.match(/SCAC_MUTATION_REGISTRY_DIGEST = ("[0-9a-f]{64}")/)[1]),
    HISTORICAL_REGISTRY_SEALS.v12.digest.slice("sha256:".length));
  assert.equal(CLAUDE_CONTINUITY_PRE_V12_DB_CATALOG_BASELINE.relation_dml.count, 295);
  assert.equal(CLAUDE_CONTINUITY_FORWARD_DB_CATALOG_BASELINE.secdef_execute.count, 355);
  assert.equal(CLAUDE_CONTINUITY_FORWARD_DB_CATALOG_BASELINE.secdef_execute.digest,
    "sha256:bb6d53a5fce3aee0b694303a346862423cb6a38efa80faf5decebb30aff3d783");
  assert.equal(CLAUDE_CONTINUITY_FORWARD_DB_CATALOG_BASELINE.runtime_dml_grants.count, 307);
  assert.match(v12Migration, /0485_claude_continuity[.]sql'[\s\S]+d9fccd80e7cd63bedfdd4c1bdf0b431882735f6cc22ed7e1445c276d2d365322/);
  assert.match(v12Migration,
    /array\['claude_continuity_leaf','claude_continuity_checkpoint','claude_continuity_revision','claude_continuity_event'\]/);
  assert.match(v12Migration,
    /alter function ops[.]scac_mutation_catalog_v11_current[(][)] rename to scac_mutation_catalog_v11_live_at_seal/);
  assert.match(v12Migration, /scac_mutation_registry_v11_seal_available[(][)]/);
  assert.match(v12Migration, /scac_mutation_registration_v12[(]text,text[)]/);
  assert.match(v12Migration, /scac_policy_epoch_snapshot_v11[(][)]/);
  assert.match(v12Migration, /registry_version='scac-mutation-registry[.]v12'[^\n]+<>1487/);
  for (const seal of Object.values(HISTORICAL_REGISTRY_SEALS)
    .filter(seal => Number(seal.version.split(".v")[1]) < 12))
    assert.match(v12Migration, new RegExp(`\\('${seal.version.replaceAll(".", "\\.")}','${seal.digest}',${seal.entryCount},${seal.sourceEntryCount}\\)`));
});

test("v13 seals the reviewed Claude startup frontier without rewriting v12", () => {
  const rows = frozenInventory(REGISTRY_V13_VERSION);
  assert.equal(rows.length, 825);
  assert.equal(generatedV13, renderRuntimeProjection(rows, {
    version: REGISTRY_V13_VERSION,
    dbCatalogBaseline: CLAUDE_STARTUP_FORWARD_DB_CATALOG_BASELINE,
  }));
  assert.equal(v13Migration, renderClaudeStartupForwardRegistrySql(rows));
  assert.equal(JSON.parse(generatedV13.match(/SCAC_MUTATION_REGISTRY_DIGEST = ("[0-9a-f]{64}")/)[1]),
    "7b2270375fe6a83d04dd3c62146db54321183d8ca202ee909e050663d2a050b8");
  assert.deepEqual(CLAUDE_STARTUP_PRE_V13_DB_CATALOG_BASELINE,
    CLAUDE_CONTINUITY_FORWARD_DB_CATALOG_BASELINE);
  assert.equal(CLAUDE_STARTUP_FORWARD_DB_CATALOG_BASELINE.secdef_execute.count, 359);
  assert.equal(CLAUDE_STARTUP_FORWARD_DB_CATALOG_BASELINE.secdef_execute.digest,
    "sha256:586dc084ef9eb234a352f1c97c69692af498b9581d1e2bd770bbd1e89f09414e");
  assert.equal(CLAUDE_STARTUP_FORWARD_DB_CATALOG_BASELINE.runtime_dml_grants.count, 307);
  assert.equal(sha256(v12Migration),
    HISTORICAL_REGISTRY_ARTIFACT_SHA256["migrations/0486_claude_continuity_registry_activation.sql"]);
  assert.equal(sha256(generatedV12),
    HISTORICAL_REGISTRY_ARTIFACT_SHA256["mcp-server/src/scac-mutation-registry.v12.generated.js"]);
  assert.match(v13Migration,
    /0486_claude_continuity_registry_activation[.]sql'[\s\S]+270f817ef74fa87bbfaa4630fc26ce313f5684a7613975c80a64cf4d25ffb127/);
  assert.match(v13Migration,
    /alter function ops[.]scac_mutation_catalog_v12_current[(][)] rename to scac_mutation_catalog_v12_live_at_seal/);
  assert.match(v13Migration, /scac_mutation_registry_v12_seal_available[(][)]/);
  assert.match(v13Migration, /scac_mutation_registration_v13[(]text,text[)]/);
  assert.match(v13Migration, /scac_mutation_catalog_v13_current[(][)]/);
  assert.match(v13Migration, /scac_policy_epoch_snapshot_v12[(][)]/);
  assert.match(v13Migration, /registry_version='scac-mutation-registry[.]v13'[^\n]+<>1491/);
  for (const seal of Object.values(HISTORICAL_REGISTRY_SEALS)
    .filter(seal => Number(seal.version.split(".v")[1]) < 13))
    assert.match(v13Migration, new RegExp(`\\('${seal.version.replaceAll(".", "\\.")}','${seal.digest}',${seal.entryCount},${seal.sourceEntryCount}\\)`));
});

test("v14 seals the Claude recovery actor hydration frontier without rewriting v13", () => {
  const rows = frozenInventory(REGISTRY_V14_VERSION);
  assert.equal(rows.length, 825);
  assert.equal(generatedV14, renderRuntimeProjection(rows, {
    version: REGISTRY_V14_VERSION,
    dbCatalogBaseline: CLAUDE_ACTOR_HYDRATION_FORWARD_DB_CATALOG_BASELINE,
  }));
  assert.equal(v14Migration, renderClaudeActorHydrationForwardRegistrySql(rows));
  assert.equal(JSON.parse(generatedV14.match(/SCAC_MUTATION_REGISTRY_DIGEST = ("[0-9a-f]{64}")/)[1]),
    "7f2987fe1dcb5bdf5bcbc269f9714261166419b992dc40f6fc446d6889e18558");
  assert.deepEqual(CLAUDE_ACTOR_HYDRATION_PRE_V14_DB_CATALOG_BASELINE,
    CLAUDE_STARTUP_FORWARD_DB_CATALOG_BASELINE);
  assert.equal(CLAUDE_ACTOR_HYDRATION_FORWARD_DB_CATALOG_BASELINE.secdef_execute.count, 363);
  assert.equal(CLAUDE_ACTOR_HYDRATION_FORWARD_DB_CATALOG_BASELINE.secdef_execute.digest,
    "sha256:c3dcffa37314df9b44f68b20a0baac5555531d3d9cf136a91020196f88234a8a");
  assert.equal(CLAUDE_ACTOR_HYDRATION_FORWARD_DB_CATALOG_BASELINE.runtime_dml_grants.count, 307);
  assert.equal(sha256(v13Migration),
    HISTORICAL_REGISTRY_ARTIFACT_SHA256["migrations/0487_claude_startup_registry_activation.sql"]);
  assert.equal(sha256(generatedV13),
    HISTORICAL_REGISTRY_ARTIFACT_SHA256["mcp-server/src/scac-mutation-registry.v13.generated.js"]);
  assert.match(v14Migration,
    /0487_claude_startup_registry_activation[.]sql'[\s\S]+04fe724c10278534638562575fda16bc5b9dc963c1478e063ba13fbf9db620aa/);
  assert.match(v14Migration,
    /alter function ops[.]scac_mutation_catalog_v13_current[(][)] rename to scac_mutation_catalog_v13_live_at_seal/);
  assert.match(v14Migration, /scac_mutation_registry_v13_seal_available[(][)]/);
  assert.match(v14Migration, /scac_mutation_registration_v14[(]text,text[)]/);
  assert.match(v14Migration, /scac_mutation_catalog_v14_current[(][)]/);
  assert.match(v14Migration, /scac_policy_epoch_snapshot_v13[(][)]/);
  assert.match(v14Migration, /registry_version='scac-mutation-registry[.]v14'[^\n]+<>1495/);
  for (const seal of Object.values(HISTORICAL_REGISTRY_SEALS)
    .filter(seal => Number(seal.version.split(".v")[1]) < 14))
    assert.match(v14Migration, new RegExp(`\\('${seal.version.replaceAll(".", "\\.")}','${seal.digest}',${seal.entryCount},${seal.sourceEntryCount}\\)`));
});

test("v15 seals Claude continuity config preservation without rewriting v14", () => {
  const rows = frozenInventory(REGISTRY_V15_VERSION);
  assert.equal(rows.length, 825);
  assert.equal(generatedV15, renderRuntimeProjection(rows, {
    version: REGISTRY_V15_VERSION,
    dbCatalogBaseline: CLAUDE_CONFIG_PRESERVATION_FORWARD_DB_CATALOG_BASELINE,
  }));
  assert.equal(v15Migration, renderClaudeConfigPreservationForwardRegistrySql(rows));
  assert.equal(JSON.parse(generatedV15.match(/SCAC_MUTATION_REGISTRY_DIGEST = ("[0-9a-f]{64}")/)[1]),
    "5f81f4579cf584a1807715f68b8297ddc4a5997a2c20906ef5300672d195360f");
  assert.deepEqual(CLAUDE_CONFIG_PRESERVATION_PRE_V15_DB_CATALOG_BASELINE,
    CLAUDE_ACTOR_HYDRATION_FORWARD_DB_CATALOG_BASELINE);
  assert.equal(CLAUDE_CONFIG_PRESERVATION_FORWARD_DB_CATALOG_BASELINE.secdef_execute.count, 367);
  assert.equal(CLAUDE_CONFIG_PRESERVATION_FORWARD_DB_CATALOG_BASELINE.secdef_execute.digest,
    "sha256:2fd333dec1d4ed6b33439e07f29fef53c86ce02a413a8121275a8e3ebc0e8064");
  assert.equal(CLAUDE_CONFIG_PRESERVATION_FORWARD_DB_CATALOG_BASELINE.runtime_dml_grants.count, 307);
  assert.equal(sha256(v14Migration),
    HISTORICAL_REGISTRY_ARTIFACT_SHA256["migrations/0488_claude_actor_hydration_registry_activation.sql"]);
  assert.equal(sha256(generatedV14),
    HISTORICAL_REGISTRY_ARTIFACT_SHA256["mcp-server/src/scac-mutation-registry.v14.generated.js"]);
  assert.match(v15Migration,
    /0488_claude_actor_hydration_registry_activation[.]sql'[\s\S]+2f170e330ab4582485e9074bbb69fdbbaeb4f2a635d6e0326f440ef1cfb8c948/);
  assert.match(v15Migration,
    /alter function ops[.]scac_mutation_catalog_v14_current[(][)] rename to scac_mutation_catalog_v14_live_at_seal/);
  assert.match(v15Migration, /scac_mutation_registry_v14_seal_available[(][)]/);
  assert.match(v15Migration, /scac_mutation_registration_v15[(]text,text[)]/);
  assert.match(v15Migration, /scac_mutation_catalog_v15_current[(][)]/);
  assert.match(v15Migration, /scac_policy_epoch_snapshot_v14[(][)]/);
  assert.match(v15Migration, /registry_version='scac-mutation-registry[.]v15'[^\n]+<>1499/);
  for (const seal of Object.values(HISTORICAL_REGISTRY_SEALS)
    .filter(seal => Number(seal.version.split(".v")[1]) < 15))
    assert.match(v15Migration, new RegExp(`\\('${seal.version.replaceAll(".", "\\.")}','${seal.digest}',${seal.entryCount},${seal.sourceEntryCount}\\)`));
});

test("v16 seals the Codex compaction checkpoint refresh without rewriting v15", () => {
  const rows = frozenInventory(REGISTRY_V16_VERSION);
  assert.equal(rows.length, 825);
  assert.equal(generatedV16, renderRuntimeProjection(rows, {
    version: REGISTRY_V16_VERSION,
    dbCatalogBaseline: CODEX_COMPACTION_CHECKPOINT_FORWARD_DB_CATALOG_BASELINE,
  }));
  assert.equal(v16Migration, renderCodexCompactionCheckpointForwardRegistrySql(rows));
  assert.equal(JSON.parse(generatedV16.match(/SCAC_MUTATION_REGISTRY_DIGEST = ("[0-9a-f]{64}")/)[1]),
    "d5418b025506b131252ddb214d75c2e1f995235db8b72ac56765485ccb5a1a54");
  assert.deepEqual(CODEX_COMPACTION_CHECKPOINT_PRE_V16_DB_CATALOG_BASELINE,
    CLAUDE_CONFIG_PRESERVATION_FORWARD_DB_CATALOG_BASELINE);
  assert.equal(CODEX_COMPACTION_CHECKPOINT_FORWARD_DB_CATALOG_BASELINE.secdef_execute.count, 371);
  assert.equal(CODEX_COMPACTION_CHECKPOINT_FORWARD_DB_CATALOG_BASELINE.secdef_execute.digest,
    "sha256:0afe988d8320a159151cd8f4673c586983d8df3d91578ef344c00e7d57bc9413");
  assert.equal(CODEX_COMPACTION_CHECKPOINT_FORWARD_DB_CATALOG_BASELINE.runtime_dml_grants.count, 307);
  assert.equal(sha256(v15Migration),
    HISTORICAL_REGISTRY_ARTIFACT_SHA256["migrations/0489_claude_config_preservation_registry_activation.sql"]);
  assert.equal(sha256(generatedV15),
    HISTORICAL_REGISTRY_ARTIFACT_SHA256["mcp-server/src/scac-mutation-registry.v15.generated.js"]);
  assert.match(v16Migration,
    /0489_claude_config_preservation_registry_activation[.]sql'[\s\S]+838be13404202e1a2077c8b52cc48a27572179f2814a3fe9c7f48e56b280cbec/);
  assert.match(v16Migration,
    /alter function ops[.]scac_mutation_catalog_v15_current[(][)] rename to scac_mutation_catalog_v15_live_at_seal/);
  assert.match(v16Migration, /scac_mutation_registry_v15_seal_available[(][)]/);
  assert.match(v16Migration, /scac_mutation_registration_v16[(]text,text[)]/);
  assert.match(v16Migration, /scac_mutation_catalog_v16_current[(][)]/);
  assert.match(v16Migration, /scac_policy_epoch_snapshot_v15[(][)]/);
  assert.match(v16Migration, /registry_version='scac-mutation-registry[.]v16'[^\n]+<>1503/);
  for (const seal of Object.values(HISTORICAL_REGISTRY_SEALS)
    .filter(seal => Number(seal.version.split(".v")[1]) < 16))
    assert.match(v16Migration, new RegExp(`\\('${seal.version.replaceAll(".", "\\.")}','${seal.digest}',${seal.entryCount},${seal.sourceEntryCount}\\)`));
});


test("v17 admits both backup helper ingresses and preserves the v16 predecessor", () => {
  const rows = frozenInventory(REGISTRY_V17_VERSION);
  assert.equal(rows.length, 827);
  for (const key of ["script-entrypoint:bin/backup-guard.py", "script-entrypoint:ops/backup-workflow-status.py"])
    assert.equal(rows.filter(row => row.ingress_key === key).length, 1);
  assert.equal(generatedV17, renderRuntimeProjection(rows, {
    version: REGISTRY_V17_VERSION,
    dbCatalogBaseline: BACKUP_GUARD_STATUS_FORWARD_DB_CATALOG_BASELINE,
  }));
  assert.equal(v17Migration, renderBackupGuardStatusForwardRegistrySql(rows));
  assert.equal(JSON.parse(generatedV17.match(/SCAC_MUTATION_REGISTRY_DIGEST = ("[0-9a-f]{64}")/)[1]),
    "5aab15679a2d26207210bde3e16be265301b9c69816e08dc90b2f2e8a48c7db2");
  assert.deepEqual(BACKUP_GUARD_STATUS_PRE_V17_DB_CATALOG_BASELINE,
    CODEX_COMPACTION_CHECKPOINT_FORWARD_DB_CATALOG_BASELINE);
  assert.equal(BACKUP_GUARD_STATUS_FORWARD_DB_CATALOG_BASELINE.secdef_execute.count, 375);
  assert.equal(BACKUP_GUARD_STATUS_FORWARD_DB_CATALOG_BASELINE.runtime_dml_grants.count, 307);
  assert.equal(sha256(v16Migration), HISTORICAL_REGISTRY_ARTIFACT_SHA256[
    "migrations/0490_codex_compaction_checkpoint_registry_activation.sql"]);
  assert.equal(sha256(generatedV16), HISTORICAL_REGISTRY_ARTIFACT_SHA256[
    "mcp-server/src/scac-mutation-registry.v16.generated.js"]);
  assert.match(v17Migration, /scac_mutation_registry_v16_seal_available[(][)]/);
  assert.match(v17Migration, /registry_version='scac-mutation-registry[.]v17'[^\n]+<>1509/);
  for (const seal of Object.values(HISTORICAL_REGISTRY_SEALS)
    .filter(seal => Number(seal.version.split(".v")[1]) < 17))
    assert.ok(v17Migration.includes(`('${seal.version}','${seal.digest}',${seal.entryCount},${seal.sourceEntryCount})`));
});

test("v18 seals the WR68 sourced shape forward correction and preserves the v17 predecessor", () => {
  const rows = frozenInventory(REGISTRY_V18_VERSION);
  assert.equal(rows.length, 827);
  assert.equal(generatedV18, renderRuntimeProjection(rows, {
    version: REGISTRY_V18_VERSION,
    dbCatalogBaseline: SOURCED_SHAPE_FORWARD_CORRECTION_FORWARD_DB_CATALOG_BASELINE,
  }));
  assert.equal(v18Migration, renderSourcedShapeForwardCorrectionRegistrySql(rows));
  assert.equal("680d42c68be736fe3f227019e3a4afd3e0aad53ed63d115db1fbb0467ea884c8",
    JSON.parse(generatedV18.match(/SCAC_MUTATION_REGISTRY_DIGEST = ("[0-9a-f]{64}")/)[1]));
  assert.deepEqual(SOURCED_SHAPE_FORWARD_CORRECTION_PRE_V18_DB_CATALOG_BASELINE,
    BACKUP_GUARD_STATUS_FORWARD_DB_CATALOG_BASELINE);
  // Four v18 registration ACLs plus the two narrow lineage-projection grants.
  assert.equal(SOURCED_SHAPE_FORWARD_CORRECTION_FORWARD_DB_CATALOG_BASELINE.secdef_execute.count, 381);
  assert.equal(SOURCED_SHAPE_FORWARD_CORRECTION_FORWARD_DB_CATALOG_BASELINE.relation_dml.count, 295);
  assert.equal(SOURCED_SHAPE_FORWARD_CORRECTION_FORWARD_DB_CATALOG_BASELINE.runtime_dml_grants.count, 307);
  assert.equal(sha256(v17Migration), HISTORICAL_REGISTRY_ARTIFACT_SHA256[
    "migrations/0491_backup_guard_status_registry_activation.sql"]);
  assert.equal(sha256(generatedV17), HISTORICAL_REGISTRY_ARTIFACT_SHA256[
    "mcp-server/src/scac-mutation-registry.v17.generated.js"]);
  for (const [path, digest] of Object.entries(SOURCED_SHAPE_FORWARD_CORRECTION_WITNESS_SHA256))
    assert.equal(sha256(fs.readFileSync(new URL(`../../${path}`, import.meta.url), "utf8")), digest, path);
  assert.match(v18Migration,
    /0491_backup_guard_status_registry_activation[.]sql'[\s\S]+49129915fe40f41400c5fc769f82633b2da68949a29d193329fba2c6016e3913/);
  assert.match(v18Migration,
    /alter function ops[.]scac_mutation_catalog_v17_current[(][)] rename to scac_mutation_catalog_v17_live_at_seal/);
  assert.match(v18Migration, /scac_mutation_registry_v17_seal_available[(][)]/);
  assert.match(v18Migration, /scac_mutation_registration_v18[(]text,text[)]/);
  assert.match(v18Migration, /scac_mutation_catalog_v18_current[(][)]/);
  assert.match(v18Migration, /scac_policy_epoch_snapshot_v17[(][)]/);
  assert.match(v18Migration, /registry_version='scac-mutation-registry[.]v18'[^\n]+<>1515/);
  assert.doesNotMatch(v18Migration, /^\s*(begin|commit)\s*;\s*$/im);
  for (const seal of Object.values(HISTORICAL_REGISTRY_SEALS)
    .filter(seal => Number(seal.version.split(".v")[1]) < 18))
    assert.ok(v18Migration.includes(`('${seal.version}','${seal.digest}',${seal.entryCount},${seal.sourceEntryCount})`));
  // The domain half precedes the registry core and follows the pre-v18 preflight.
  const domain = renderSourcedShapeForwardCorrectionDomainSql();
  const preflightAt = v18Migration.indexOf("do $sourced_shape_forward_correction_preflight$");
  const domainAt = v18Migration.indexOf(domain);
  const coreAt = v18Migration.indexOf("-- SCAC-12: forward-only mutation registry v18 after sourced shape forward correction.");
  assert.ok(preflightAt >= 0 && preflightAt < domainAt && domainAt < coreAt);
  assert.equal(v18Migration.indexOf(domain, domainAt + 1), -1);
});

test("v19 seals the WR69 incident/work-request association and preserves the v18 predecessor", () => {
  const rows = frozenInventory(REGISTRY_V19_VERSION);
  assert.equal(rows.length, 828);
  assert.equal(generatedV19, renderRuntimeProjection(rows, {
    version: REGISTRY_V19_VERSION,
    dbCatalogBaseline: INCIDENT_WORK_REQUEST_LINK_FORWARD_DB_CATALOG_BASELINE,
  }));
  assert.equal(v19Migration, renderIncidentWorkRequestLinkRegistrySql(rows));
  // v19 is sealed history now that the active import is the v20 successor (the
  // live binding is asserted by "the ACTIVE runtime registry is v20"). Bind the
  // generated v19 artifact to its FROZEN seal rather than to the live import,
  // so this keeps proving v19 immutability instead of silently re-following
  // whatever the runtime currently points at.
  assert.equal(
    `sha256:${JSON.parse(generatedV19.match(/SCAC_MUTATION_REGISTRY_DIGEST = ("[0-9a-f]{64}")/)[1])}`,
    HISTORICAL_REGISTRY_SEALS.v19.digest);
  assert.deepEqual(INCIDENT_WORK_REQUEST_LINK_PRE_V19_DB_CATALOG_BASELINE,
    SOURCED_SHAPE_FORWARD_CORRECTION_FORWARD_DB_CATALOG_BASELINE);
  assert.equal(INCIDENT_WORK_REQUEST_LINK_FORWARD_DB_CATALOG_BASELINE.secdef_execute.count, 385);
  assert.equal(INCIDENT_WORK_REQUEST_LINK_FORWARD_DB_CATALOG_BASELINE.relation_dml.count, 295);
  assert.equal(INCIDENT_WORK_REQUEST_LINK_FORWARD_DB_CATALOG_BASELINE.runtime_dml_grants.count, 307);
  assert.equal(sha256(v18Migration), HISTORICAL_REGISTRY_ARTIFACT_SHA256[
    "migrations/0492_sourced_shape_forward_correction_and_scac_successor.sql"]);
  assert.equal(sha256(generatedV18), HISTORICAL_REGISTRY_ARTIFACT_SHA256[
    "mcp-server/src/scac-mutation-registry.v18.generated.js"]);
  assert.match(v19Migration,
    /0492_sourced_shape_forward_correction_and_scac_successor[.]sql'[\s\S]+3c38ac9b0b22984603f58838aabcf97094e451ad166bc8526b09273f3f9755c6/);
  assert.match(v19Migration,
    /alter function ops[.]scac_mutation_catalog_v18_current[(][)] rename to scac_mutation_catalog_v18_live_at_seal/);
  assert.match(v19Migration, /scac_mutation_registry_v18_seal_available[(][)]/);
  assert.match(v19Migration, /scac_mutation_registration_v19[(]text,text[)]/);
  assert.match(v19Migration, /scac_mutation_catalog_v19_current[(][)]/);
  assert.match(v19Migration, /scac_policy_epoch_snapshot_v18[(][)]/);
  assert.match(v19Migration, /registry_version='scac-mutation-registry[.]v19'[^\n]+<>1520/);
  assert.doesNotMatch(v19Migration, /^\s*(begin|commit)\s*;\s*$/im);
  // v19 is the seal this migration CREATES, so it carries every predecessor
  // tuple through v18 and not its own.
  for (const seal of Object.values(HISTORICAL_REGISTRY_SEALS)) {
    const tuple = `('${seal.version}','${seal.digest}',${seal.entryCount},${seal.sourceEntryCount})`;
    assert.equal(v19Migration.includes(tuple), seal.version !== REGISTRY_V19_VERSION, seal.version);
  }
  const domain = renderIncidentWorkRequestLinkDomainSql();
  const preflightAt = v19Migration.indexOf("do $incident_work_request_link_preflight$");
  const domainAt = v19Migration.indexOf(domain);
  const coreAt = v19Migration.indexOf("-- SCAC-12: forward-only mutation registry v19 after incident work-request link.");
  assert.ok(preflightAt >= 0 && preflightAt < domainAt && domainAt < coreAt);
  assert.equal(v19Migration.indexOf(domain, domainAt + 1), -1);
  assert.match(domain, /incident_evidence jsonb/);
  assert.match(domain, /unresolved_occurrence_edge_count/);
  assert.match(domain, /from ops[.]v_trace t/);
  assert.match(domain, /as association/);
});

// A test-only variant of ops/scac-mutation-inventory.mjs has to keep resolving
// its sibling ops modules and its ops/config fixtures, so the isolated tree is
// a symlink shadow of ops/ rather than a bare directory. Writing the variants
// outside ops/ is the point: a stray .mjs beside the real one is exactly the
// fixture leak the v3 correction was written to avoid.
function linkOpsTree(repoRoot, isolatedRoot) {
  const opsRoot = path.join(isolatedRoot, "ops");
  fs.mkdirSync(opsRoot);
  for (const entry of fs.readdirSync(path.join(repoRoot, "ops"), { withFileTypes: true }))
    fs.symlinkSync(path.join(repoRoot, "ops", entry.name), path.join(opsRoot, entry.name),
      entry.isDirectory() ? "dir" : "file");
  return opsRoot;
}

test("v20 seals the Codex continuity archive frontier and preserves the v19 predecessor", () => {
  const rows = frozenInventory(REGISTRY_V20_VERSION);
  assert.equal(rows.length, 828);
  assert.equal(generatedV20, renderRuntimeProjection(rows, {
    version: REGISTRY_V20_VERSION,
    dbCatalogBaseline: CONTINUITY_ARCHIVE_FORWARD_DB_CATALOG_BASELINE,
  }));
  assert.equal(v20Migration, renderContinuityArchiveForwardRegistrySql(rows));
  assert.deepEqual(CONTINUITY_ARCHIVE_PRE_V20_DB_CATALOG_BASELINE,
    INCIDENT_WORK_REQUEST_LINK_FORWARD_DB_CATALOG_BASELINE);
  assert.equal(CONTINUITY_ARCHIVE_PRE_V20_DB_CATALOG_BASELINE.projection_version,
    "scac-db-catalog-projection.v19");
  assert.equal(CONTINUITY_ARCHIVE_FORWARD_DB_CATALOG_BASELINE.projection_version,
    "scac-db-catalog-projection.v20");
  // The registry-only successor adds four security-definer functions and touches
  // no relation, column, role or grant: every other category is INHERITED from
  // the v19 receipt rather than restated.
  assert.equal(CONTINUITY_ARCHIVE_FORWARD_DB_CATALOG_BASELINE.secdef_execute.count, 389);
  for (const category of ["relation_dml", "column_dml", "role_authority", "runtime_dml_grants"])
    assert.deepEqual(CONTINUITY_ARCHIVE_FORWARD_DB_CATALOG_BASELINE[category],
      CONTINUITY_ARCHIVE_PRE_V20_DB_CATALOG_BASELINE[category], category);
  assert.equal(sha256(v19Migration), HISTORICAL_REGISTRY_ARTIFACT_SHA256[
    "migrations/0493_incident_work_request_link_scac_successor.sql"]);
  assert.equal(sha256(generatedV19), HISTORICAL_REGISTRY_ARTIFACT_SHA256[
    "mcp-server/src/scac-mutation-registry.v19.generated.js"]);
  assert.deepEqual(HISTORICAL_REGISTRY_SEALS.v19, {
    version: REGISTRY_V19_VERSION,
    digest: "sha256:19c1c9967bf960a64cefa39c53f6011193180f0c65128a1d8d5987ea6e120841",
    entryCount: 1520,
    sourceEntryCount: 828,
  });
  assert.match(v20Migration,
    /alter function ops[.]scac_mutation_catalog_v19_current[(][)] rename to scac_mutation_catalog_v19_live_at_seal/);
  assert.match(v20Migration, /scac_mutation_registry_v19_seal_available[(][)]/);
  assert.match(v20Migration, /scac_mutation_registration_v20[(]text,text[)]/);
  assert.match(v20Migration, /scac_mutation_catalog_v20_current[(][)]/);
  assert.match(v20Migration, /scac_policy_epoch_snapshot_v19[(][)]/);
  assert.match(v20Migration, /registry-only mutation registry v20 after Codex continuity archive recovery/);
  assert.doesNotMatch(v20Migration, /^\s*(begin|commit)\s*;\s*$/im);
  assert.doesNotMatch(v20Migration, /__V19_|__V20_|UNBOUND/);
  // Registry-only: no domain DDL, no business rows, no new store, no grant change.
  assert.doesNotMatch(v20Migration, /create table (?!if not exists ops[.]scac_)/);
  assert.doesNotMatch(v20Migration, /do \$incident_work_request_link_preflight\$/);
  assert.match(v20Migration, /do \$continuity_archive_preflight\$/);
  for (const seal of Object.values(HISTORICAL_REGISTRY_SEALS))
    assert.ok(v20Migration.includes(
      `('${seal.version}','${seal.digest}',${seal.entryCount},${seal.sourceEntryCount})`), seal.version);
});

test("the ACTIVE runtime registry is v20, and a stale v19 import fails admission", async () => {
  // mutation-registry.js is the module every TOOLS admission actually runs
  // through, so this binds the LIVE import rather than the mere existence of a
  // generated v20 file. Re-pinning the generated artifact without re-pointing
  // this import is exactly the miss this test exists to catch.
  assert.equal(SCAC_MUTATION_REGISTRY_VERSION, REGISTRY_V20_VERSION);
  const v20GeneratedDigest = generatedV20.match(
    /^export const SCAC_MUTATION_REGISTRY_DIGEST = "([0-9a-f]{64})";$/m)[1];
  assert.equal(SCAC_MUTATION_REGISTRY_DIGEST, v20GeneratedDigest);
  assert.notEqual(`sha256:${SCAC_MUTATION_REGISTRY_DIGEST}`, HISTORICAL_REGISTRY_SEALS.v19.digest);

  // The decisive stale-import catch, and the reason this is a RUNTIME test and
  // not a string check: v20 re-derived codex-read-recovery's schema_digest.
  // assertRegisteredOperation recomputes that digest from the LIVE tool's
  // inputSchema and refuses on mismatch, so an import still pointing at v19
  // makes real admission throw mutation_contract_mismatch here.
  const name = "codex-read-recovery";
  const tool = TOOLS[name];
  assert.ok(tool, `${name} must be a live registered tool`);
  const v19Row = frozenInventory(REGISTRY_V19_VERSION).find(row => row.operation === name);
  const v20Row = frozenInventory(REGISTRY_V20_VERSION).find(row => row.operation === name);
  // Non-vacuity: the two versions genuinely disagree about this operation, so
  // passing below cannot be satisfied by either version indifferently.
  assert.notEqual(v19Row.schema_digest, v20Row.schema_digest);

  const admitted = await assertRegisteredOperation(name, tool, {});
  assert.equal(admitted.schema_digest, v20Row.schema_digest);
  assert.equal(registeredOperation(name).schema_digest, v20Row.schema_digest);
  assert.notEqual(registeredOperation(name).schema_digest, v19Row.schema_digest);

  // Prove the refusal path is real: the v19 contract for this operation, fed to
  // the same admission check, is rejected rather than quietly tolerated.
  await assert.rejects(
    () => assertRegisteredOperation(name, { ...tool, registrySource: "mcp-server/src/stale.js" }, {}),
    error => error instanceof MutationRegistryRefusal
      && error.error === "mutation_contract_mismatch" && error.operation === name,
  );

  // Every operation v20 re-derived must be admitted by the active registry.
  for (const operation of ["codex-checkpoint", "codex-read-recovery", "codex-record-event"]) {
    const live = TOOLS[operation];
    assert.ok(live, `${operation} must be a live registered tool`);
    const row = await assertRegisteredOperation(operation, live, {});
    assert.equal(row.ingress_key, `mcp-tool:${operation}`);
    assert.equal(row.source_digest,
      frozenInventory(REGISTRY_V20_VERSION).find(r => r.operation === operation).source_digest);
  }
});

test("a partial or absent v20 predecessor bundle regenerates the same successor", () => {
  const rows = frozenInventory(REGISTRY_V20_VERSION);
  const baseline = CONTINUITY_ARCHIVE_FORWARD_DB_CATALOG_BASELINE;
  const bundle = { migration: v19Migration, runtime: generatedV19 };
  const supplied = renderContinuityArchiveForwardRegistrySql(rows, baseline, bundle);
  assert.equal(supplied, v20Migration);
  assert.equal(renderContinuityArchiveForwardRegistrySql(
    rows, baseline, { migration: bundle.migration }), supplied);
  assert.equal(renderContinuityArchiveForwardRegistrySql(
    rows, baseline, { runtime: bundle.runtime }), supplied);
  assert.equal(renderContinuityArchiveForwardRegistrySql(rows, baseline), supplied);
  for (const [half, pathName] of [
    ["migration", "migrations/0493_incident_work_request_link_scac_successor.sql"],
    ["runtime", "mcp-server/src/scac-mutation-registry.v19.generated.js"],
  ])
    assert.throws(() => renderContinuityArchiveForwardRegistrySql(rows, baseline,
      { ...bundle, [half]: `${bundle[half]}\n-- tampered\n` }),
      new RegExp(`sealed historical SCAC v19 artifact changed: ${pathName.replace(/[/.]/g, "\\$&")}`));
});

test("the v20 successor refuses a caller-supplied predecessor or wrong projection", () => {
  const rows = frozenInventory(REGISTRY_V20_VERSION);
  const baseline = CONTINUITY_ARCHIVE_FORWARD_DB_CATALOG_BASELINE;
  // The trust root takes no parameters at all, so there is no door through which
  // a caller could hand it its own expected seal, pin or catalog.
  assert.equal(assertContinuityArchiveV20TrustRoot.length, 0);
  assert.doesNotThrow(() => assertContinuityArchiveV20TrustRoot());
  for (const wrong of [
    CONTINUITY_ARCHIVE_PRE_V20_DB_CATALOG_BASELINE,
    { ...baseline, projection_version: "scac-db-catalog-projection.v21" },
    { ...baseline, projection_version: undefined },
  ])
    assert.throws(() => renderContinuityArchiveForwardRegistrySql(rows, wrong),
      /successor v20 catalog baseline is not scac-db-catalog-projection[.]v20/);
  for (const category of [
    "secdef_execute", "relation_dml", "column_dml", "role_authority", "runtime_dml_grants",
  ]) {
    assert.throws(() => renderContinuityArchiveForwardRegistrySql(rows,
      { ...baseline, [category]: { count: null, digest: "__V20_UNBOUND__" } }),
      new RegExp(`successor v20 ${category} receipt is unbound`));
    assert.throws(() => renderContinuityArchiveForwardRegistrySql(rows,
      { ...baseline, [category]: { ...baseline[category], digest: "sha256:zz" } }),
      new RegExp(`successor v20 ${category} receipt is unbound`));
  }
});

test("every v20 output path refuses an unbound trust-root limb and writes nothing", () => {
  // Each case UNBINDS exactly one limb of the committed module and proves the
  // shared guard fires on both the frontier and the CLI writer. The anchors are
  // read off the live constants, so a future rebinding cannot leave this test
  // silently matching nothing.
  const seal = HISTORICAL_REGISTRY_SEALS.v19;
  const receipt = CONTINUITY_ARCHIVE_FORWARD_DB_CATALOG_BASELINE.secdef_execute;
  const unbind = {
    seal: [
      `digest: "${seal.digest}", entryCount: ${seal.entryCount}, sourceEntryCount: ${seal.sourceEntryCount}`,
      'digest: "__V19_SEALED_REGISTRY_DIGEST_UNBOUND__", entryCount: null, sourceEntryCount: null',
      /continuity archive v20 predecessor seal is unbound/,
    ],
    pin: [
      `"${HISTORICAL_REGISTRY_ARTIFACT_SHA256["migrations/0493_incident_work_request_link_scac_successor.sql"]}"`,
      '"__V19_MIGRATION_SHA256_UNBOUND__"',
      /continuity archive v20 predecessor artifact pin is unbound: migrations\/0493/,
    ],
    receipt: [
      `secdef_execute: { count: ${receipt.count}, digest: "${receipt.digest}" }`,
      'secdef_execute: { count: null, digest: "__V20_SECDEF_EXECUTE_RECEIPT_UNBOUND__" }',
      /continuity archive successor v20 secdef_execute receipt is unbound/,
    ],
  };
  const repoRoot = fileURLToPath(new URL("../../", import.meta.url));
  const source = fs.readFileSync(path.join(repoRoot, "ops/scac-mutation-inventory.mjs"), "utf8");
  const isolatedRoot = fs.mkdtempSync(path.join(repoRoot, ".tmp.v20-trust-root-"));
  try {
    linkOpsTree(repoRoot, isolatedRoot);
    for (const [limb, [bound, unboundText, refusal]] of Object.entries(unbind)) {
      const modulePath = path.join(isolatedRoot, `ops/scac-mutation-inventory.${limb}.mjs`);
      fs.writeFileSync(modulePath,
        replaceExactlyOnce(source, bound, unboundText, `unbind ${limb}`));
      const target = path.join(isolatedRoot, `${limb}.v20.generated.js`);
      assert.throws(() => execFileSync(process.execPath,
        [modulePath, "--write-runtime-v20", target],
        { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }),
        error => {
          assert.match(error.stderr, refusal, limb);
          return true;
        }, limb);
      assert.equal(fs.existsSync(target), false, `${limb}: a refused run must write nothing`);
      assert.throws(() => execFileSync(process.execPath,
        [modulePath, "--write-continuity-archive-registry-migration",
          path.join(isolatedRoot, `${limb}.0494.sql`)],
        { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }),
        error => {
          assert.match(error.stderr, refusal, limb);
          return true;
        }, limb);
      assert.equal(fs.existsSync(path.join(isolatedRoot, `${limb}.0494.sql`)), false, limb);
    }
    // ANTI-VACUITY: the same CLI on the committed module renders the committed
    // artifact byte for byte, so the refusals above are the guard and not a
    // broken harness.
    const boundTarget = path.join(isolatedRoot, "bound.v20.generated.js");
    execFileSync(process.execPath,
      [path.join(repoRoot, "ops/scac-mutation-inventory.mjs"), "--write-runtime-v20", boundTarget],
      { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
    assert.equal(fs.readFileSync(boundTarget, "utf8"), generatedV20);
  } finally {
    fs.rmSync(isolatedRoot, { recursive: true, force: true });
  }
});

test("the frontier refuses an unbound trust root before any predecessor work runs", async () => {
  const seal = HISTORICAL_REGISTRY_SEALS.v19;
  const repoRoot = fileURLToPath(new URL("../../", import.meta.url));
  const source = fs.readFileSync(path.join(repoRoot, "ops/scac-mutation-inventory.mjs"), "utf8");
  const isolatedRoot = fs.mkdtempSync(path.join(repoRoot, ".tmp.v20-frontier-guard-"));
  try {
    linkOpsTree(repoRoot, isolatedRoot);
    // Sabotage the FIRST predecessor renderer the cascade reaches. If the guard
    // ran late we would see the sabotage marker instead of the refusal.
    const sabotage = [
      "function renderMigration(rows = fullInventory()) {",
      'function renderMigration(rows = fullInventory()) {\n  throw new Error("SABOTAGE_PREDECESSOR_WORK_RAN");',
      "sabotage predecessor",
    ];
    const unboundPath = path.join(isolatedRoot, "ops/scac-mutation-inventory.sabotage.mjs");
    fs.writeFileSync(unboundPath, replaceExactlyOnce(
      replaceExactlyOnce(source,
        `digest: "${seal.digest}", entryCount: ${seal.entryCount}, sourceEntryCount: ${seal.sourceEntryCount}`,
        'digest: "__V19_SEALED_REGISTRY_DIGEST_UNBOUND__", entryCount: null, sourceEntryCount: null',
        "unbind seal"),
      ...sabotage));
    const unbound = await import(unboundPath);
    assert.throws(() => unbound.renderGeneratedFrontier(),
      /continuity archive v20 predecessor seal is unbound/);
    // ANTI-VACUITY: with the trust root left bound, the very same sabotage IS
    // reached — so the refusal above is ordering, not an unreachable branch.
    const reachablePath = path.join(isolatedRoot, "ops/scac-mutation-inventory.reachable.mjs");
    fs.writeFileSync(reachablePath, replaceExactlyOnce(source, ...sabotage));
    const reachable = await import(reachablePath);
    assert.throws(() => reachable.renderGeneratedFrontier(), /SABOTAGE_PREDECESSOR_WORK_RAN/);
  } finally {
    fs.rmSync(isolatedRoot, { recursive: true, force: true });
  }
});

test("the complete source-only frontier is byte-reproducible from frozen inputs", () => {
  assert.equal(assertCurrentSourceInventoryMatchesFixture(TOOLS), true);
  const paths = assertGeneratedFrontierMatchesCommitted();
  const migrations = paths.filter(path => path.startsWith("migrations/")).sort();
  assert.equal(migrations.length, 28);
  assert.deepEqual(migrations.map(path => path.match(/migrations\/(\d{4})_/)[1]),
    [...Array.from({ length: 18 }, (_, index) => String(454 + index).padStart(4, "0")), "0481", "0486", "0487", "0488", "0489", "0490", "0491", "0492", "0493", "0494"]);
  assert.equal(paths.filter(path => path.endsWith(".generated.js")).length, 19);
  assert.equal(paths.length, 47);
});

test("the complete frontier renders when every generated target is absent", () => {
  const repoRoot = fileURLToPath(new URL("../../", import.meta.url));
  const isolatedRoot = fs.mkdtempSync(path.join(repoRoot, ".tmp.wr48-targetless-"));
  const outputRoot = path.join(isolatedRoot, "generated");
  const frontier = renderGeneratedFrontier();
  const frontierPaths = Object.keys(frontier);
  const frontierSet = new Set(frontierPaths);
  try {
    const trackedPaths = parseGitIndexEntries(execFileSync("git", ["ls-files", "--stage", "-z"], {
      cwd: repoRoot,
      encoding: "buffer",
    })).map(entry => entry.path);
    for (const trackedPath of trackedPaths) {
      if (frontierSet.has(trackedPath)) continue;
      const source = path.join(repoRoot, trackedPath);
      const target = path.join(isolatedRoot, trackedPath);
      fs.mkdirSync(path.dirname(target), { recursive: true });
      fs.copyFileSync(source, target);
    }
    assert.equal(frontierPaths.filter(target => fs.existsSync(path.join(isolatedRoot, target))).length, 0);
    const stdout = execFileSync(process.execPath, [
      path.join(isolatedRoot, "ops/scac-mutation-inventory.mjs"),
      "--write-generated-frontier",
      outputRoot,
    ], {
      cwd: isolatedRoot,
      encoding: "utf8",
      env: {
        ...process.env,
        GIT_DIR: path.join(repoRoot, ".git"),
        GIT_WORK_TREE: isolatedRoot,
      },
    });
    assert.match(stdout, /\(47 artifacts\)/);
    for (const [target, expected] of Object.entries(frontier))
      assert.equal(fs.readFileSync(path.join(outputRoot, target), "utf8"), expected, target);
  } finally {
    fs.rmSync(isolatedRoot, { recursive: true, force: true });
  }
});

test("every direct catalog redefinition preserves the portable role census", () => {
  for (const [name, sql] of directRegistryRedefinitions) {
    assert.match(sql, /rolname~'\^carr_' and rolname<>'carr_ci' and not rolcanlogin and not rolsuper/, name);
    assert.match(sql, /mem\.rolname~'\^carr_' and \(g\.rolsuper or g\.rolname~'\^\(neon_\|pg_\)'\)/, name);
    assert.match(sql, /return observed_count=12 and observed_digest='sha256:eb650de73032466b46787f4a5826b60b100591657489a7990d9161e2d6588648'/, name);
    assert.doesNotMatch(sql, /observed_count=95|082b8570b428c33296c801871177f6bfb34e9c070513d4b1db23007f4edecafb/, name);
  }
  assert.equal((siep18MonitorMigration.match(/a\.grantee<>c\.relowner/g) || []).length, 8);
});

test("unknown, changed, and open operation contracts refuse deterministically", async () => {
  const source = TOOLS["add-loop"];
  await assert.rejects(
    assertRegisteredOperation("not-reviewed", source, {}),
    error => error instanceof MutationRegistryRefusal && error.error === "unregistered_operation",
  );
  await assert.rejects(
    assertRegisteredOperation("add-loop", { ...source, write: !source.write }, {}),
    error => error instanceof MutationRegistryRefusal && error.error === "mutation_contract_mismatch",
  );
  assert.throws(
    () => assertClosedTopLevel("add-loop", source, { text: "safe", actor: "joe" }),
    error => error instanceof MutationRegistryRefusal && error.error === "unregistered_operation_fields",
  );
  assert.equal(registeredOperation("not-reviewed"), null);
});

test("the sealed v11 registry admits the real Codex checkpoint contract", async () => {
  const sealedDigest =
    "c19344c49e59fb799584ccdd2829053c7b43b8a072d72fd90b2c759a9e17b760";
  assert.equal(sha256(TOOLS["codex-checkpoint"].inputSchema), sealedDigest);
  const operation = await assertRegisteredOperation(
    "codex-checkpoint",
    TOOLS["codex-checkpoint"],
    {
      idempotency_key: "00000000-0000-4000-8000-000000000001",
      runtime: "codex",
      native_task_id: "task-1",
      project_id: "project-1",
      cwd: "/repo",
      expected_version: 0,
      state: { objective: "continue", next_action: "verify" },
    },
  );
  assert.equal(operation.schema_digest, sealedDigest);
});

test("composites expose exact reviewed edges and generic dispatch stays default deny", () => {
  assert.deepEqual(registeredOperation("stamp-touch").delegates_to, ["log-activity"]);
  assert.deepEqual(registeredOperation("resolve-candidate").delegates_to,
    ["log-activity", "new-deal", "patch-deal-field", "set-next-step"]);
  assert.deepEqual(registeredOperation("find-and-catch-up").delegates_to, ["catch-me-up", "find"]);
  assert.deepEqual(registeredOperation("prepare-conversation").delegates_to,
    ["find-and-catch-up", "who-do-we-know"]);
  assert.deepEqual(registeredOperation("morning-brief").delegates_to,
    ["claim-card", "deal-room-board", "loop-board", "today-triage"]);
  assert.deepEqual(registeredOperation("call-verb").delegates_to, ["*registered_operation"]);
  assert.match(fs.readFileSync(new URL("../src/tools.js", import.meta.url), "utf8"),
    /executeRegisteredTool\(c, actor, "log-activity"/);
});

test("migration is read-only at runtime and preserves the SIEP-18 boundary", () => {
  assert.match(migration, /security definer set search_path=pg_catalog,ops/);
  assert.match(migration, /revoke all on ops\.scac_mutation_registry_version,ops\.scac_mutation_registry_entry from public,carr_reader,carr_writer,carr_jobs,carr_authority/);
  assert.match(migration, /grant execute on function ops\.scac_mutation_registration\(text,text\) to carr_reader,carr_writer,carr_jobs,carr_authority/);
  assert.doesNotMatch(migration, /grant (?:insert|update|delete|all) on ops\.scac_mutation_registry/i);
  assert.match(migration, /atomic_database_mediation_operational boolean not null check \(not atomic_database_mediation_operational\)/);
  assert.match(migration, /mcp_default_deny_source_guarded boolean not null/);
  assert.match(migration, /db_metadata_authority boolean not null check \(db_metadata_authority\)/);
  assert.match(migration, /runtime_projection_authorizing boolean not null check \(not runtime_projection_authorizing\)/);
  assert.match(migration, /non_mcp_default_deny_operational boolean not null check \(not non_mcp_default_deny_operational\)/);
  assert.match(migration, /direct_database_grant_cutover boolean not null check \(not direct_database_grant_cutover\)/);
  assert.match(migration, /production_enforcement_active boolean not null check \(not production_enforcement_active\)/);
  assert.match(migration, /before insert or update or delete on ops\.scac_mutation_registry_entry/);
});

test("reviewed non-MCP source locators resolve and remain explicitly non-authorizing", () => {
  const rows = fullInventory(TOOLS).filter(row => !["mcp_tool", "job_definition", "workflow_entrypoint"].includes(row.ingress_kind));
  assert.equal(rows.length, 543);
  for (const row of rows) {
    assert.equal(fs.existsSync(new URL(`../../${row.source_locator}`, import.meta.url)), true,
      `${row.source_locator} must resolve`);
    assert.equal(row.classification_authorizing, false);
    assert.equal(row.implementation_state, "inventoried_not_atomically_mediated");
  }
  const scripts = discoverScriptEntrypoints();
  assert.equal(scripts.length, 534);
  assert.equal(scripts.some(path => path === "ops/rule-delivery-cutover.py"), true);
  assert.equal(scripts.some(path => path === "ops/control-plane-scheduler-cutover.py"), true);
  assert.equal(scripts.some(path => path === "run.sh"), true);
  assert.equal(scripts.some(path => path === "mcp-server/local-verb.mjs"), true);
  assert.equal(scripts.some(path => path === "hooks/scheduled-run-record.py"), true);
  assert.equal(scripts.some(path => path === "pipelines/backfill_lease_event.py"), true);
  assert.equal(scripts.some(path => path === "pipelines/import_brokers.py"), true);
  assert.equal(scripts.some(path => path === "ops/sync_control_catalog.py"), true);
  assert.equal(scripts.some(path => path === "ops/githooks/pre-commit"), true);
  assert.equal(scripts.some(path => path === "ops/githooks/pre-push"), true);
  assert.equal(scripts.some(path => path === "ops/githooks/commit-msg"), true);
  for (const path of [
    "ops/capture-verb-reachability.py",
    "ops/delegation-telemetry-report.py",
    "ops/gate-lifecycle-report.py",
    "ops/rule-triage-apply.py",
    "ops/rule-triage-report.py",
    "ops/zz-engineering-controller-concurrency-gate.py",
    "hooks/canonical-edit-gate.py",
    "ops/untracked-anomaly-report.py",
  ]) assert.equal(scripts.includes(path), true, `${path} must be registered`);
  /* canonical-edit-gate.py moved to the PRESENCE list above when Joe ruled
     REINSTATE (decision 7f48abf6, 2026-09-01; Repo Hygiene Program R02). The
     assertion itself stays: it is the record of PR #722's decision, and these
     two gates are still retired -- R02 reinstated one of the three. */
  for (const path of [
    "hooks/git-writer-gate.py",
    "hooks/staging-attribution-gate.py",
  ]) assert.equal(scripts.includes(path), false, `${path} was retired upstream`);
  assert.equal(scripts.some(path => path.includes("selftest") || path.includes("/test/")), false);
  assert.deepEqual(rows.filter(row => ["script_entrypoint", "external_admin", "break_glass"].includes(row.ingress_kind))
    .map(row => row.source_locator).sort(), scripts);
  assert.equal(rows.find(row => row.source_locator === "tools/db-tap.py").ingress_kind, "break_glass");
  assert.equal(rows.find(row => row.source_locator === "tools/call-verb.py").ingress_kind, "break_glass");
  assert.equal(rows.find(row => row.source_locator === "tools/run-breakglass.py").ingress_kind, "break_glass");
  assert.equal(rows.find(row => row.source_locator === "tools/migrate-prod-support.py").ingress_kind, "external_admin");
  assert.deepEqual(rows.find(row => row.source_locator === "run.sh").delegates_to,
    ["*registered_script_entrypoint"]);
  assert.deepEqual(rows.find(row => row.source_locator === "mcp-server/local-verb.mjs").delegates_to,
    ["*registered_mcp_tool"]);
});

test("script discovery is bound to tracked index paths and index executable modes", () => {
  const parsed = parseGitIndexEntries(Buffer.from(
    "100755 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 0\tbin/tracked-entry\0" +
    "100644 bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb 0\tops/tracked.py\0" +
    "100755 cccccccccccccccccccccccccccccccccccccccc 1\tbin/unmerged\0",
  ));
  assert.deepEqual(parsed, [
    { path: "bin/tracked-entry", executable: true },
    { path: "ops/tracked.py", executable: false },
  ]);
  assert.equal(parsed.some(({ path }) => path === "tmp/untracked.py"), false,
    "ambient untracked files cannot enter an index-derived census");
  assert.equal(isScriptEntrypoint("bin/tracked-entry", true, "#!/bin/sh\nexit 0\n"), true);
  assert.equal(isScriptEntrypoint("bin/tracked-entry", false, "#!/bin/sh\nexit 0\n"), false,
    "extensionless entrypoints use the Git index mode, never host stat bits");
  assert.equal(isScriptEntrypoint("ops/tracked.py", false,
    "if __name__ == '__main__':\n    main()\n"), true);
});

test("job definitions and live DB capabilities have exact reviewed baselines", () => {
  const jobs = jobDefinitionInventory();
  assert.equal(jobs.length, JOB_DEFINITION_BASELINE.count);
  assert.equal(jobs.every(row => row.ingress_kind === "job_definition" && row.entrypoint), true);
  assert.deepEqual(DB_CATALOG_BASELINE, {
    projection_version: "scac-db-catalog-projection.v1",
    secdef_execute: { count: 290, digest: "sha256:92d1347b45ee669c97a8b21712684651ee67aa3a2af363fca7c3f3a25436a0b6" },
    relation_dml: { count: 285, digest: "sha256:53d12ebf83db4661b0e55eb81f91ab510c34828424a2b945b66c0286134b0b0b" },
    column_dml: { count: 12, digest: "sha256:607e31d990653776243350d001ca465234e321349b05259751f8231ae3c2c44f" },
  });
  assert.match(migration, /ingress_kind','db_function_acl'/);
  assert.match(migration, /ingress_kind','db_relation_acl'/);
  assert.match(migration, /ingress_kind','db_column_acl'/);
  assert.match(migration, /entry_set_digest/);
  assert.match(migration, /ops\.scac_canonical_json\(contract\)/);
  assert.match(migration, /for category,kind in values \('secdef_execute','db_function_acl'\)/);
  assert.match(migration, /actual_digest<>expected->>'digest'/);
  assert.deepEqual(SIEP12_DB_CATALOG_BASELINE.role_authority, {
    count: 12,
    digest: "sha256:eb650de73032466b46787f4a5826b60b100591657489a7990d9161e2d6588648",
  });
  assert.match(successorMigration, /pg_auth_members/);
});

test("GitHub and launchd workflow entrances bind exact triggers, permissions, and delegates", () => {
  const workflows = workflowDefinitionInventory();
  assert.equal(workflows.length, 31);
  const github = workflows.filter(row => row.source_locator.startsWith(".github/workflows/"));
  assert.equal(github.length, 7);
  assert.equal(github.every(row => row.ingress_kind === "workflow_entrypoint" &&
    row.trigger_contract_digest && row.permissions_contract_digest && row.classification_authorizing === false), true);
  const automerge = workflows.find(row => row.source_locator === ".github/workflows/automerge-pilot.yml");
  assert.equal(automerge.delegates_to.includes("script:ops/automerge_pilot.py"), true);
  const backup = workflows.find(row => row.source_locator === ".github/workflows/backup-nightly.yml");
  assert.equal(backup.delegates_to.includes("shell:aws-s3api-put-object"), true);
  const dbAcceptance = workflows.find(row => row.source_locator === ".github/workflows/db-acceptance.yml");
  assert.equal(dbAcceptance.delegates_to.includes("script:ops/local-pg-ci.py"), true);
  const launchd = workflows.filter(row => row.source_locator.startsWith("ops/launchd/"));
  assert.equal(launchd.length, 24);
  assert.equal(launchd.every(row => row.launchd_label && row.trigger_contract_digest &&
    row.program_arguments_digest && row.physical_authority_refs.some(ref => ref.startsWith("ops.service_environment:")) &&
    row.classification_authorizing === false), true);
  assert.equal(launchd.flatMap(row => row.physical_authority_refs)
    .filter(ref => ref.startsWith("ops.service_environment:")).length, 25);
  assert.equal(launchd.find(row => row.launchd_label === "com.carr.rules-refresh")
    .physical_authority_refs.includes("ops.service_environment:rules-refresh:production"), true);
  assert.deepEqual(launchd.flatMap(row => row.physical_authority_refs)
    .filter(ref => ref.startsWith("ops.legacy_schedule_launchd_contract:")).sort(), [
      "ops.legacy_schedule_launchd_contract:calendar-fetch-daily.launchd.v1",
      "ops.legacy_schedule_launchd_contract:nightly-record-layer.launchd.v1",
      "ops.legacy_schedule_launchd_contract:notes-sweep-hourly.launchd.v1",
    ]);
  const rules = launchd.find(row => row.launchd_label === "com.carr.rules-refresh");
  assert.equal(rules.delegates_to.includes("script:bin/run-scheduled.sh"), true);
  assert.equal(rules.delegates_to.includes("script:bin/refresh-rules.sh"), true);
  const partnerPing = launchd.find(row => row.launchd_label === "com.carr.partner-ping");
  assert.equal(partnerPing.delegates_to.includes("script:pipelines/partner_ping.py"), true);
  const callMode = launchd.find(row => row.launchd_label === "com.carr.call-mode");
  assert.equal(callMode.delegates_to.includes("script:tools/dictation-rig/bin/call-mode.py"), true);
  const scripts = new Set(discoverScriptEntrypoints());
  for (const delegate of launchd.flatMap(row => row.delegates_to).filter(value => value.startsWith("script:")))
    assert.equal(scripts.has(delegate.slice("script:".length)), true, `${delegate} must resolve`);
  const rulesSource = fs.readFileSync(new URL("../../ops/launchd/com.carr.rules-refresh.plist", import.meta.url), "utf8");
  const reviewedPlist = parsePlistXml(rulesSource);
  const changedPlist = parsePlistXml(rulesSource.replace("<integer>8</integer>", "<integer>88</integer>"));
  assert.equal(reviewedPlist.StartCalendarInterval.length, 14);
  assert.equal(reviewedPlist.StartCalendarInterval[1].Hour, 8);
  assert.equal(changedPlist.StartCalendarInterval[1].Hour, 88);
  assert.notDeepEqual(reviewedPlist.StartCalendarInterval, changedPlist.StartCalendarInterval);
  assert.match(migration, /'workflow_entrypoint'/);
});

test("launchd physical-authority catalogs are bidirectionally closed and source-exact", () => {
  const launchdPaths = fs.readdirSync(new URL("../../ops/launchd/", import.meta.url))
    .filter(name => name.endsWith(".plist")).map(name => `ops/launchd/${name}`).sort();
  const services = JSON.parse(fs.readFileSync(
    new URL("../../ops/config/services.json", import.meta.url), "utf8"));
  const legacy = JSON.parse(fs.readFileSync(
    new URL("../../ops/config/control-plane-scheduler-cutover.v1.json", import.meta.url), "utf8"));
  assert.doesNotThrow(() => validateLaunchdAuthorityCatalogs(launchdPaths, services, legacy));

  const missingService = structuredClone(services);
  const rules = missingService.services.find(service => service.key === "rules-refresh");
  rules.environments = rules.environments.filter(environment =>
    environment.deploy_mechanism !== "ops/launchd/com.carr.rules-refresh.plist");
  assert.throws(() => validateLaunchdAuthorityCatalogs(launchdPaths, missingService, legacy),
    /catalog closure mismatch/);

  const orphanService = structuredClone(services);
  orphanService.services[0].environments.push({
    environment: "local", deploy_mechanism: "ops/launchd/com.carr.orphan.plist",
  });
  assert.throws(() => validateLaunchdAuthorityCatalogs(launchdPaths, orphanService, legacy),
    /orphan=ops\/launchd\/com\.carr\.orphan\.plist/);

  const duplicateLegacy = structuredClone(legacy);
  duplicateLegacy.surfaces.push({
    ...duplicateLegacy.surfaces.find(surface => surface.scheduler_kind === "launchd"),
    surface_id: "duplicate.launchd.v1",
  });
  assert.throws(() => validateLaunchdAuthorityCatalogs(launchdPaths, services, duplicateLegacy),
    /duplicate launchd legacy path/);

  const legacySurface = legacy.surfaces.find(surface =>
    surface.repo_plist_relpath === "ops/launchd/com.carr.rules-refresh.plist") ||
    legacy.surfaces.find(surface => surface.scheduler_kind === "launchd");
  const plist = parsePlistXml(fs.readFileSync(
    new URL(`../../${legacySurface.repo_plist_relpath}`, import.meta.url), "utf8"));
  assert.doesNotThrow(() => assertLegacyLaunchdSource(legacySurface, legacySurface.repo_plist_relpath, plist));
  assert.throws(() => assertLegacyLaunchdSource(
    { ...legacySurface, canonical_program_arguments: [...legacySurface.canonical_program_arguments, "--forged"] },
    legacySurface.repo_plist_relpath, plist), /legacy source mismatch/);
});
