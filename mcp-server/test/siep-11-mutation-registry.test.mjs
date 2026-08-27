import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import test from "node:test";

import {
  assertLegacyLaunchdSource,
  DB_CATALOG_BASELINE,
  discoverScriptEntrypoints,
  fullInventory,
  jobDefinitionInventory,
  JOB_DEFINITION_BASELINE,
  mcpInventory,
  parsePlistXml,
  registryDigest,
  REGISTRY_V5_VERSION,
  renderMigration,
  renderRuntimeProjection,
  renderSIEP15RegistrySql,
  SIEP15_DB_CATALOG_BASELINE,
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
  new URL("../../migrations/0338_siep11_mutation_registry.sql", import.meta.url), "utf8");
const generated = fs.readFileSync(
  new URL("../src/scac-mutation-registry.generated.js", import.meta.url), "utf8");
const generatedV2 = fs.readFileSync(
  new URL("../src/scac-mutation-registry.v2.generated.js", import.meta.url), "utf8");
const successorMigration = fs.readFileSync(
  new URL("../../migrations/0339_siep12_policy_epoch.sql", import.meta.url), "utf8");
const generatedV3 = fs.readFileSync(
  new URL("../src/scac-mutation-registry.v3.generated.js", import.meta.url), "utf8");
const v3Migration = fs.readFileSync(
  new URL("../../migrations/0341_siep13_forward_mutation_registry.sql", import.meta.url), "utf8");
const generatedV4 = fs.readFileSync(
  new URL("../src/scac-mutation-registry.v4.generated.js", import.meta.url), "utf8");
const v4Migration = fs.readFileSync(
  new URL("../../migrations/0343_siep14_forward_mutation_registry.sql", import.meta.url), "utf8");
const generatedV5 = fs.readFileSync(
  new URL("../src/scac-mutation-registry.v5.generated.js", import.meta.url), "utf8");
const v5Migration = fs.readFileSync(
  new URL("../../migrations/0347_siep15_forward_mutation_registry.sql", import.meta.url), "utf8");

test("reviewed MCP inventory is an exact immutable projection of the assembled registry", () => {
  const rows = mcpInventory();
  assert.equal(rows.length, 185);
  assert.equal(rows.filter(row => row.write).length, 128);
  assert.equal(rows.filter(row => !row.write).length, 57);
  assert.deepEqual(rows.map(row => row.operation), Object.keys(TOOLS).sort());
  assert.equal(Object.isFrozen(TOOLS), true);
  assert.equal(Object.isFrozen(TOOLS["add-loop"]), true);
  assert.equal(Object.isFrozen(TOOLS["add-loop"].inputSchema), true);
  assert.throws(() => { TOOLS["add-loop"].handler = async () => ({ ok: true }); }, TypeError);
  assert.equal(new Set(rows.map(row => row.ingress_key)).size, rows.length);
  assert.equal(new Set(rows.map(row => row.schema_digest)).has(undefined), false);
});

test("sealed v1/v2/v3/v4 stay historical while live source and DB projections advance exactly to v5", () => {
  const rows = fullInventory();
  assert.equal(crypto.createHash("sha256").update(migration).digest("hex"),
    "b8e6f76ab926eae7cc2ee64a9505be7fa4d65837c48a9bcf4c040c6dad6fc714");
  assert.match(generated,
    /SCAC_MUTATION_REGISTRY_DIGEST = "d821ab892e4f9aeb97c4dfc040fd9e072c5d009685b1521fd463cc8268df5038"/);
  assert.equal(SCAC_MUTATION_REGISTRY_DIGEST,
    JSON.parse(generatedV5.match(/SCAC_MUTATION_REGISTRY_DIGEST = ("[0-9a-f]{64}")/)[1]));
  assert.match(generated, /SCAC_MUTATION_REGISTRY_VERSION = "scac-mutation-registry\.v1"/);
  assert.equal(crypto.createHash("sha256").update(successorMigration).digest("hex"),
    "23cc10c2074afea130142486c131eef6096d55e36bd3cbb1fc52e0c67a6daaeb");
  assert.equal(crypto.createHash("sha256").update(generatedV2).digest("hex"),
    "8775f50745dacd234a0b399ab705404abb65fddd4b29fbea55f8f0d3e753f1af");
  assert.equal(crypto.createHash("sha256").update(generatedV3).digest("hex"),
    "384c7a9a7bb900666ef1436317946ec0d2fc92b91e6ffee11a31118544319b8a");
  assert.equal(crypto.createHash("sha256").update(v3Migration).digest("hex"),
    "9bbd01e19a81d36f31965b0ebc0685e18faaeb00c7208a4f0cf931fbaf5231dd");
  assert.equal(crypto.createHash("sha256").update(generatedV4).digest("hex"),
    "b6b9c5dc68123fac4da707d7a8c6e78d7f09f0a12a647d2190e8e2194c69f3cf");
  assert.equal(crypto.createHash("sha256").update(v4Migration).digest("hex"),
    "93bcf7e33f303c7abbfa3c27a61662f95fe9c9b00a4caf6870674945a0fed98b");
  assert.equal(generatedV5, renderRuntimeProjection(rows, {
    version: REGISTRY_V5_VERSION, dbCatalogBaseline: SIEP15_DB_CATALOG_BASELINE,
  }));
  assert.equal(v5Migration, renderSIEP15RegistrySql(rows));
  assert.match(successorMigration, /scac-mutation-registry\.v1/);
  assert.match(successorMigration, /scac-mutation-registry\.v2/);
  assert.match(v3Migration, /scac-mutation-registry\.v3/);
  assert.match(v4Migration, /scac-mutation-registry\.v4/);
  assert.match(v5Migration, /scac-mutation-registry\.v5/);
  assert.match(v5Migration,
    /alter function ops\.scac_mutation_catalog_v4_current\(\) rename to scac_mutation_catalog_v4_live_at_seal/);
  assert.match(v5Migration, /create or replace function ops\.scac_mutation_registry_seal_valid\(p_registry_version text\)/);
  assert.match(v5Migration, /ops\.scac_mutation_registry_seal_valid\('scac-mutation-registry\.v1'\)/);
  assert.match(v5Migration, /ops\.scac_mutation_registry_seal_valid\('scac-mutation-registry\.v2'\)/);
  assert.match(v5Migration, /ops\.scac_mutation_registry_seal_valid\('scac-mutation-registry\.v3'\)/);
  assert.match(v5Migration, /ops\.scac_mutation_registry_v4_seal_available\(\)/);
  assert.match(v5Migration, /source:=ops\.scac_policy_epoch_snapshot_v3\(\)/);
  assert.doesNotMatch(v5Migration, /source:=ops\.scac_policy_epoch_snapshot_v4\(\)/);
  assert.match(v5Migration,
    /\('scac-mutation-registry\.v1','sha256:d821ab892e4f9aeb97c4dfc040fd9e072c5d009685b1521fd463cc8268df5038',1230,729\)/);
  assert.match(v5Migration,
    /\('scac-mutation-registry\.v2','sha256:92f9bc98b90eb9a678facfd9b8c7d28eb72b4864c1d603e6f274c39103d35231',1235,730\)/);
  assert.match(v5Migration,
    /\('scac-mutation-registry\.v3','sha256:dcdf3f9617bf5f595df4dc6977e7b35e3316ce4fd29deb26d0bae77b4f025fec',1240,731\)/);
  assert.match(v5Migration,
    /\('scac-mutation-registry\.v4','sha256:dc605d6d2290ac683ad2d544ad1e1ebe0d6983064a3dd232915c1c5342f8504b',1245,732\)/);
  assert.doesNotMatch(v5Migration, /Retain v2 as an exact historical lookup after the live catalog advances/);
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
  const rows = fullInventory().filter(row => !["mcp_tool", "job_definition", "workflow_entrypoint"].includes(row.ingress_kind));
  assert.equal(rows.length, 494);
  for (const row of rows) {
    assert.equal(fs.existsSync(new URL(`../../${row.source_locator}`, import.meta.url)), true,
      `${row.source_locator} must resolve`);
    assert.equal(row.classification_authorizing, false);
    assert.equal(row.implementation_state, "inventoried_not_atomically_mediated");
  }
  const scripts = discoverScriptEntrypoints();
  assert.equal(scripts.length, 485);
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
  assert.equal(scripts.some(path => path.includes("selftest") || path.includes("/test/")), false);
  assert.deepEqual(rows.filter(row => ["script_entrypoint", "external_admin", "break_glass"].includes(row.ingress_kind))
    .map(row => row.source_locator).sort(), scripts);
  assert.equal(rows.find(row => row.source_locator === "tools/db-tap.py").ingress_kind, "break_glass");
  assert.equal(rows.find(row => row.source_locator === "tools/call-verb.py").ingress_kind, "break_glass");
  assert.deepEqual(rows.find(row => row.source_locator === "run.sh").delegates_to,
    ["*registered_script_entrypoint"]);
  assert.deepEqual(rows.find(row => row.source_locator === "mcp-server/local-verb.mjs").delegates_to,
    ["*registered_mcp_tool"]);
});

test("job definitions and live DB capabilities have exact reviewed baselines", () => {
  const jobs = jobDefinitionInventory();
  assert.equal(jobs.length, JOB_DEFINITION_BASELINE.count);
  assert.equal(jobs.every(row => row.ingress_kind === "job_definition" && row.entrypoint), true);
  assert.deepEqual(DB_CATALOG_BASELINE, {
    projection_version: "scac-db-catalog-projection.v1",
    secdef_execute: { count: 205, digest: "sha256:394508cc8ad50bf7193d857a36fcb35bfa601eccbcf35e70c4fff6c119b5b562" },
    relation_dml: { count: 284, digest: "sha256:3bb06a15f3f19914d476edd5a2c789e307b5298633c2d4d98c1a3e5c10359345" },
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
    count: 52,
    digest: "sha256:345871802aa8f5b57aa87f3edfeac5187d06be0cb1ab5695371bcdfba4a49433",
  });
  assert.match(successorMigration, /pg_auth_members/);
});

test("GitHub and launchd workflow entrances bind exact triggers, permissions, and delegates", () => {
  const workflows = workflowDefinitionInventory();
  assert.equal(workflows.length, 28);
  const github = workflows.filter(row => row.source_locator.startsWith(".github/workflows/"));
  assert.equal(github.length, 5);
  assert.equal(github.every(row => row.ingress_kind === "workflow_entrypoint" &&
    row.trigger_contract_digest && row.permissions_contract_digest && row.classification_authorizing === false), true);
  const automerge = workflows.find(row => row.source_locator === ".github/workflows/automerge-pilot.yml");
  assert.equal(automerge.delegates_to.includes("script:ops/automerge_pilot.py"), true);
  const backup = workflows.find(row => row.source_locator === ".github/workflows/backup-nightly.yml");
  assert.equal(backup.delegates_to.includes("shell:aws-s3api-put-object"), true);
  const launchd = workflows.filter(row => row.source_locator.startsWith("ops/launchd/"));
  assert.equal(launchd.length, 23);
  assert.equal(launchd.every(row => row.launchd_label && row.trigger_contract_digest &&
    row.program_arguments_digest && row.physical_authority_refs.some(ref => ref.startsWith("ops.service_environment:")) &&
    row.classification_authorizing === false), true);
  assert.equal(launchd.flatMap(row => row.physical_authority_refs)
    .filter(ref => ref.startsWith("ops.service_environment:")).length, 24);
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
