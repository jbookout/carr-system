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
  replaceExactlyOnce,
  renderGeneratedFrontier,
  renderRuntimeProjection,
  renderSIEP16IntegratedRegistrySql,
  renderSIEP17ForwardRegistrySql,
  renderSIEP18ForwardRegistrySql,
  renderSourceMergeForwardRegistrySql,
  sha256,
  SIEP16_INTEGRATED_DB_CATALOG_BASELINE,
  SIEP17_FORWARD_DB_CATALOG_BASELINE,
  SIEP18_FORWARD_DB_CATALOG_BASELINE,
  SIEP18_MONITOR_ARTIFACT_SHA256,
  SIEP18_PRE_V9_DB_CATALOG_BASELINE,
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
  assert.equal(rows.length, 221);
  assert.equal(rows.filter(row => row.write).length, 155);
  assert.equal(rows.filter(row => !row.write).length, 66);
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
  const seals = Object.values(HISTORICAL_REGISTRY_SEALS).filter(seal => seal.version !== REGISTRY_V9_VERSION);
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
  assert.equal(SCAC_MUTATION_REGISTRY_DIGEST,
    JSON.parse(generatedV10.match(/SCAC_MUTATION_REGISTRY_DIGEST = ("[0-9a-f]{64}")/)[1]));
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

test("the complete source-only frontier is byte-reproducible from frozen inputs", () => {
  assert.equal(assertCurrentSourceInventoryMatchesFixture(TOOLS), true);
  const paths = assertGeneratedFrontierMatchesCommitted();
  const migrations = paths.filter(path => path.startsWith("migrations/")).sort();
  assert.equal(migrations.length, 18);
  assert.deepEqual(migrations.map(path => path.match(/migrations\/(\d{4})_/)[1]),
    Array.from({ length: 18 }, (_, index) => String(454 + index).padStart(4, "0")));
  assert.equal(paths.filter(path => path.endsWith(".generated.js")).length, 9);
  assert.equal(paths.length, 27);
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
    assert.match(stdout, /\(27 artifacts\)/);
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
  assert.equal(rows.length, 536);
  for (const row of rows) {
    assert.equal(fs.existsSync(new URL(`../../${row.source_locator}`, import.meta.url)), true,
      `${row.source_locator} must resolve`);
    assert.equal(row.classification_authorizing, false);
    assert.equal(row.implementation_state, "inventoried_not_atomically_mediated");
  }
  const scripts = discoverScriptEntrypoints();
  assert.equal(scripts.length, 527);
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
