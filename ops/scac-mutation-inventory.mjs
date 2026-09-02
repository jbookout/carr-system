#!/usr/bin/env node

import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { TOOLS } from "../mcp-server/src/tools.js";
import { renderPolicyEpochMigration } from "./scac-policy-epoch-sql.mjs";

export const REGISTRY_VERSION = "scac-mutation-registry.v1";
export const REGISTRY_V2_VERSION = "scac-mutation-registry.v2";
export const REGISTRY_V3_VERSION = "scac-mutation-registry.v3";
export const REGISTRY_V4_VERSION = "scac-mutation-registry.v4";
export const REGISTRY_V5_VERSION = "scac-mutation-registry.v5";
export const REGISTRY_V6_VERSION = "scac-mutation-registry.v6";
export const REGISTRY_V7_VERSION = "scac-mutation-registry.v7";
export const REGISTRY_V8_VERSION = "scac-mutation-registry.v8";
export const REGISTRY_V9_VERSION = "scac-mutation-registry.v9";
export const REGISTRY_V10_VERSION = "scac-mutation-registry.v10";
const REPO_ROOT = fileURLToPath(new URL("../", import.meta.url));
// v1-v9 remain source-only until Joe approves Production application. Their
// active post-main tail may be regenerated only through the explicit
// --write-rebased-* commands below; ordinary historical write modes stay
// refused so an accidental invocation cannot rewrite a reviewed seal.
export const HISTORICAL_REGISTRY_SEALS = Object.freeze({
  v1: Object.freeze({ version: REGISTRY_VERSION, digest: "sha256:7cc2feacec82bf7cce2af9af309dc4ae9426922003471703af010f6728957190", entryCount: 1387, sourceEntryCount: 800 }),
  v2: Object.freeze({ version: REGISTRY_V2_VERSION, digest: "sha256:d78696444c3f9d6dc9f82b71469986671b3846b762be7096ef4370bf4f6d609e", entryCount: 1391, sourceEntryCount: 800 }),
  v3: Object.freeze({ version: REGISTRY_V3_VERSION, digest: "sha256:b5b32aeb7c777cf726c5cb526a860a5b7d644afa54c3ce1fd55a42132fc0b298", entryCount: 1395, sourceEntryCount: 800 }),
  v4: Object.freeze({ version: REGISTRY_V4_VERSION, digest: "sha256:1bdf011d492a611eb528ddb907292871d099fea507e4fca11646978944a42c91", entryCount: 1399, sourceEntryCount: 800 }),
  v5: Object.freeze({ version: REGISTRY_V5_VERSION, digest: "sha256:18a0bcb000edd6bad0f82b229748592173c6de31b18663718418a2d5fd95b36b", entryCount: 1404, sourceEntryCount: 800 }),
  v6: Object.freeze({ version: REGISTRY_V6_VERSION, digest: "sha256:9538ff0f43a6b5cbd7bcabb0f79bd78852e1f9c23f29291b42b25f61035f9dc2", entryCount: 1408, sourceEntryCount: 800 }),
  v7: Object.freeze({ version: REGISTRY_V7_VERSION, digest: "sha256:0662449e25ab7cb26eb2ba922d4e2177b22e62066c8d5a3eac01daa9879a1aec", entryCount: 1412, sourceEntryCount: 800 }),
  v8: Object.freeze({ version: REGISTRY_V8_VERSION, digest: "sha256:aac53ad8a01b1e2bbf4cd633b6f4cacdf4d66195556be31a64b0795091d4f397", entryCount: 1425, sourceEntryCount: 800 }),
  v9: Object.freeze({ version: REGISTRY_V9_VERSION, digest: "sha256:af77a09256a23a31c616e89136eb2adb861fef99a2068f2950c223d6d1828dc6", entryCount: 1439, sourceEntryCount: 800 }),
});
export const HISTORICAL_REGISTRY_ARTIFACT_SHA256 = Object.freeze({
  "migrations/0454_siep11_mutation_registry.sql": "7985d42b9b36964b33503f4ff42d332e6bcce085217f06464a9d6abf58126bdd",
  "migrations/0455_siep12_policy_epoch.sql": "ad3e10165c764799fea2b70f7328e6bc935f737216e03921726363175245adc6",
  "migrations/0457_siep13_forward_mutation_registry.sql": "010d94e1ad6be6d34209492a54e6b9497702e6e3d2203448466df00602cec8a8",
  "migrations/0459_siep14_forward_mutation_registry.sql": "1a4980d92f8f024147d57619fa7eb4858805a3314cd61ea8eb0f8a0f1704a2ef",
  "mcp-server/src/scac-mutation-registry.generated.js": "e8cf336806337ba0ba25532816692ac2a24b48f9df58cee2966baaeafdae5abc",
  "mcp-server/src/scac-mutation-registry.v2.generated.js": "7e2e680f5eb0c20aa01dab605d9040c2b71841f75bf51fd9a02468ca1c2da23d",
  "mcp-server/src/scac-mutation-registry.v3.generated.js": "45e03d5c8fde6f44067cc2a6ec97b262f251182f77a95efdeafdcf29adc9e0c5",
  "mcp-server/src/scac-mutation-registry.v4.generated.js": "bcece72c3089b3970128e306094836b3c88f5d8f41c0e1b92262eee2662c918f",
  "migrations/0461_siep15_forward_mutation_registry.sql": "ef1d65080c684275278c380e867abfef1d3ce101f31061e8653f6f716a2b9694",
  "mcp-server/src/scac-mutation-registry.v5.generated.js": "ad999f53bb954cda52708050845946e2beddcc2fa3cc44239336a043d2279cf3",
  "migrations/0462_siep16_forward_mutation_registry.sql": "bdcd95597a966a9828b29d3c0875b747bcc781f4351816899d533e87901aadea",
  "mcp-server/src/scac-mutation-registry.v6.generated.js": "be5c28d7ab1cc7f6b00ae2c556ab4359ec8a1f3ef0d64f3b57ecf8ab43a25137",
  "migrations/0464_siep16_integrated_mutation_registry.sql": "83b442d96e2a73cf34efc5384994a60fc0df11ff899a3c03ec3f9d343490c288",
  "mcp-server/src/scac-mutation-registry.v7.generated.js": "b2f8bee1c5027d97cfaf9930b7b9bf0130b51802f1af398e5f0c1867911ab45d",
  "migrations/0466_siep17_forward_mutation_registry.sql": "f23d89bf7862b67eb2eb979a3eb3bf0947fb170c5f02cd83b4f316f4897bec13",
  "mcp-server/src/scac-mutation-registry.v8.generated.js": "2388c79ae79b76b433ce69e237fd43d850deeebfb42cf787490882191ae8ddf0",
  "migrations/0468_siep18_forward_mutation_registry.sql": "2edee53cbfc70e8585ac9004a93ba0002728c88972bc1aedad840f411b55e794",
  "mcp-server/src/scac-mutation-registry.v9.generated.js": "5d01afe2f0e035441f3f8ec406db8a53557eb536652624609d151ce12f09706e",
});
// 0467 is the reviewed SIEP-18 monitor source consumed by the v9 generator.
// It is not historical yet, but its bytes must still be exact: otherwise a
// mutable monitor function could silently change the generated 0468 artifact.
export const SIEP18_MONITOR_ARTIFACT_SHA256 =
  "c42fa1b8b0916c80c7c17d9f74fb2bb0baf7469f9e855d8ccb31fcd91a07db8b";
export const DB_CATALOG_BASELINE = Object.freeze({
  projection_version: "scac-db-catalog-projection.v1",
  secdef_execute: { count: 290, digest: "sha256:92d1347b45ee669c97a8b21712684651ee67aa3a2af363fca7c3f3a25436a0b6" },
  relation_dml: { count: 285, digest: "sha256:53d12ebf83db4661b0e55eb81f91ab510c34828424a2b945b66c0286134b0b0b" },
  column_dml: { count: 12, digest: "sha256:607e31d990653776243350d001ca465234e321349b05259751f8231ae3c2c44f" },
});
// SIEP-12's forward-only registry successor. These values are recomputed from
// a disposable database after every 0455 authority-surface change, never from
// a caller or a running Production catalog.
export const SIEP12_DB_CATALOG_BASELINE = Object.freeze({
  projection_version: "scac-db-catalog-projection.v2",
  secdef_execute: { count: 294, digest: "sha256:8e1f8ed8984bc1f1a627020d1b5b0384124c5e5adba79ac17df0b21702bf6cc5" },
  relation_dml: { count: 285, digest: "sha256:53d12ebf83db4661b0e55eb81f91ab510c34828424a2b945b66c0286134b0b0b" },
  column_dml: { count: 12, digest: "sha256:607e31d990653776243350d001ca465234e321349b05259751f8231ae3c2c44f" },
  role_authority: { count: 95, digest: "sha256:082b8570b428c33296c801871177f6bfb34e9c070513d4b1db23007f4edecafb" },
});
// SIEP-13's forward-only successor includes the v3 typed registry lookup.
// The digest is replaced only from disposable-DB readback when 0341 changes.
export const SIEP13_DB_CATALOG_BASELINE = Object.freeze({
  projection_version: "scac-db-catalog-projection.v3",
  secdef_execute: { count: 298, digest: "sha256:d8306fc5bb4bd3348fe5197f0946c251c4f0486ecdf98914ba11a7af6e4682c5" },
  relation_dml: { count: 285, digest: "sha256:53d12ebf83db4661b0e55eb81f91ab510c34828424a2b945b66c0286134b0b0b" },
  column_dml: { count: 12, digest: "sha256:607e31d990653776243350d001ca465234e321349b05259751f8231ae3c2c44f" },
  role_authority: { count: 95, digest: "sha256:082b8570b428c33296c801871177f6bfb34e9c070513d4b1db23007f4edecafb" },
});
// SIEP-14's successor includes the read-only v4 registry projection. The
// category digest is replaced only from disposable-DB readback.
export const SIEP14_DB_CATALOG_BASELINE = Object.freeze({
  projection_version: "scac-db-catalog-projection.v4",
  secdef_execute: { count: 302, digest: "sha256:abe2fb9009736fa4ae47270fcf5d2153bbf3e7a8ed01de861cced9a1bb2fee82" },
  relation_dml: { count: 285, digest: "sha256:53d12ebf83db4661b0e55eb81f91ab510c34828424a2b945b66c0286134b0b0b" },
  column_dml: { count: 12, digest: "sha256:607e31d990653776243350d001ca465234e321349b05259751f8231ae3c2c44f" },
  role_authority: { count: 95, digest: "sha256:082b8570b428c33296c801871177f6bfb34e9c070513d4b1db23007f4edecafb" },
});
// SIEP-15 adds the safe device-status projection and the v5 registry lookup.
// The secdef digest starts fail-closed and is replaced only by disposable-DB readback.
export const SIEP15_DB_CATALOG_BASELINE = Object.freeze({
  projection_version: "scac-db-catalog-projection.v5",
  secdef_execute: { count: 307, digest: "sha256:d4671fbc10a54406acece348dbeaef08b044b8231037bf4ecce8e1d85cd2d24d" },
  relation_dml: { count: 285, digest: "sha256:53d12ebf83db4661b0e55eb81f91ab510c34828424a2b945b66c0286134b0b0b" },
  column_dml: { count: 12, digest: "sha256:607e31d990653776243350d001ca465234e321349b05259751f8231ae3c2c44f" },
  role_authority: { count: 95, digest: "sha256:082b8570b428c33296c801871177f6bfb34e9c070513d4b1db23007f4edecafb" },
});
// SIEP-16 itself is source-only. This forward successor records the measured
// post-main database catalog and the current source inventory without
// rewriting the now-historical v5 artifacts.
export const SIEP16_DB_CATALOG_BASELINE = Object.freeze({
  projection_version: "scac-db-catalog-projection.v6",
  secdef_execute: { count: 311, digest: "sha256:78771a23004af05b61ea9491ed70248ee8cbbe77aa6ace8c6f0437983624470b" },
  relation_dml: { count: 285, digest: "sha256:53d12ebf83db4661b0e55eb81f91ab510c34828424a2b945b66c0286134b0b0b" },
  column_dml: { count: 12, digest: "sha256:607e31d990653776243350d001ca465234e321349b05259751f8231ae3c2c44f" },
  role_authority: { count: 95, digest: "sha256:082b8570b428c33296c801871177f6bfb34e9c070513d4b1db23007f4edecafb" },
});
// Upstream source integration after the v6 seal adds source entrances. The v7
// successor also adds five read-only registry projection functions; the
// database digest is replaced only from disposable-DB readback.
export const SIEP16_INTEGRATED_DB_CATALOG_BASELINE = Object.freeze({
  projection_version: "scac-db-catalog-projection.v7",
  secdef_execute: { count: 315, digest: "sha256:d47181d79ffb352fdf2c707a6fa265f093a8c7edde76df4358a6805c89651022" },
  relation_dml: { count: 285, digest: "sha256:53d12ebf83db4661b0e55eb81f91ab510c34828424a2b945b66c0286134b0b0b" },
  column_dml: { count: 12, digest: "sha256:607e31d990653776243350d001ca465234e321349b05259751f8231ae3c2c44f" },
  role_authority: { count: 95, digest: "sha256:082b8570b428c33296c801871177f6bfb34e9c070513d4b1db23007f4edecafb" },
});
// SIEP-17's forward successor includes the token/challenge authority source
// surface. Values are measured from disposable-DB readback only.
export const SIEP17_FORWARD_DB_CATALOG_BASELINE = Object.freeze({
  projection_version: "scac-db-catalog-projection.v8",
  secdef_execute: { count: 328, digest: "sha256:7c6a54dda8f8c4c6f4fcb6004f2544ba8231f212e1b2d8e5c9cc39651a2a216c" },
  relation_dml: { count: 285, digest: "sha256:53d12ebf83db4661b0e55eb81f91ab510c34828424a2b945b66c0286134b0b0b" },
  column_dml: { count: 12, digest: "sha256:607e31d990653776243350d001ca465234e321349b05259751f8231ae3c2c44f" },
  role_authority: { count: 95, digest: "sha256:082b8570b428c33296c801871177f6bfb34e9c070513d4b1db23007f4edecafb" },
});
// The disposable-DB receipt before 0468 is applied. 0468 must first seal this
// exact predecessor catalog, then its four security-definer self-effects yield
// the v9 baseline below. Keeping both measurements makes the delta reviewable.
export const SIEP18_PRE_V9_DB_CATALOG_BASELINE = Object.freeze({
  projection_version: "scac-db-catalog-projection.v8-post-0467",
  secdef_execute: { count: 338, digest: "sha256:ccf023867a696884b2b9e50ae6eccc7b4e2afd9d7d6dbd1a93c01d8b1ec38555" },
  relation_dml: { count: 285, digest: "sha256:53d12ebf83db4661b0e55eb81f91ab510c34828424a2b945b66c0286134b0b0b" },
  column_dml: { count: 12, digest: "sha256:607e31d990653776243350d001ca465234e321349b05259751f8231ae3c2c44f" },
  role_authority: { count: 95, digest: "sha256:082b8570b428c33296c801871177f6bfb34e9c070513d4b1db23007f4edecafb" },
});
// SIEP-18's forward successor binds the exact post-0468 catalog and the
// separately measured runtime DML grant snapshot. The grant snapshot is a
// derived monitor input and therefore does not add registry entry rows.
export const SIEP18_FORWARD_DB_CATALOG_BASELINE = Object.freeze({
  projection_version: "scac-db-catalog-projection.v9",
  secdef_execute: { count: 342, digest: "sha256:57444b408258e9ec0a0dd8d2b8062cc6f6575e0b97cd0e9faebbb7ca322e17af" },
  relation_dml: { count: 285, digest: "sha256:53d12ebf83db4661b0e55eb81f91ab510c34828424a2b945b66c0286134b0b0b" },
  column_dml: { count: 12, digest: "sha256:607e31d990653776243350d001ca465234e321349b05259751f8231ae3c2c44f" },
  role_authority: { count: 95, digest: "sha256:082b8570b428c33296c801871177f6bfb34e9c070513d4b1db23007f4edecafb" },
  runtime_dml_grants: { count: 297, digest: "sha256:0f04a50d8bc65e2dcc765b1981ab1d5091c809570f0a773db3f5c6e2b9d43501" },
});
// The exact catalog after 0470 and before the v10 registration function is
// installed. The forward successor refuses before creating any v10 surface if
// this disposable-DB receipt is no longer exact.
export const SOURCE_MERGE_PRE_V10_DB_CATALOG_BASELINE = Object.freeze({
  projection_version: "scac-db-catalog-projection.v9-post-0470",
  secdef_execute: { count: 343, digest: "sha256:5a43e0558b6559dbb2461fbb9064424330698cf0a8dc5a347652fb0774195669" },
  relation_dml: { count: 285, digest: "sha256:53d12ebf83db4661b0e55eb81f91ab510c34828424a2b945b66c0286134b0b0b" },
  column_dml: { count: 12, digest: "sha256:607e31d990653776243350d001ca465234e321349b05259751f8231ae3c2c44f" },
  role_authority: { count: 95, digest: "sha256:082b8570b428c33296c801871177f6bfb34e9c070513d4b1db23007f4edecafb" },
  runtime_dml_grants: { count: 297, digest: "sha256:0f04a50d8bc65e2dcc765b1981ab1d5091c809570f0a773db3f5c6e2b9d43501" },
});
// The successor's registration ACL changes the security-definer projection by
// four entries. Its digest is replaced only from disposable-DB readback.
export const SOURCE_MERGE_FORWARD_DB_CATALOG_BASELINE = Object.freeze({
  projection_version: "scac-db-catalog-projection.v10",
  secdef_execute: { count: 347, digest: "sha256:03624b669043c5e2e5a81633837f29ffb096b824a840456b26d7b6f3b405b467" },
  relation_dml: { count: 285, digest: "sha256:53d12ebf83db4661b0e55eb81f91ab510c34828424a2b945b66c0286134b0b0b" },
  column_dml: { count: 12, digest: "sha256:607e31d990653776243350d001ca465234e321349b05259751f8231ae3c2c44f" },
  role_authority: { count: 95, digest: "sha256:082b8570b428c33296c801871177f6bfb34e9c070513d4b1db23007f4edecafb" },
  runtime_dml_grants: { count: 297, digest: "sha256:0f04a50d8bc65e2dcc765b1981ab1d5091c809570f0a773db3f5c6e2b9d43501" },
});
export const JOB_DEFINITION_BASELINE = Object.freeze({
  count: 26,
  digest: "sha256:152742893824c64275a99326335f2b8ca97cf592153c5cb280b353adfa15eb91",
});

export function canonicalize(value) {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === "object")
    return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonicalize(value[key])]));
  return value;
}

export function sha256(value) {
  const bytes = typeof value === "string" ? value : JSON.stringify(canonicalize(value));
  return createHash("sha256").update(bytes).digest("hex");
}

export function replaceExactlyOnce(value, search, replacement, label) {
  const first = value.indexOf(search);
  const second = first < 0 ? -1 : value.indexOf(search, first + search.length);
  if (first < 0 || second >= 0)
    throw new Error(`${label} marker count must be exactly one`);
  return `${value.slice(0, first)}${replacement}${value.slice(first + search.length)}`;
}

function delegatesTo(name) {
  if (name === "stamp-touch") return ["log-activity"];
  if (name === "resolve-candidate") return ["log-activity", "new-deal", "patch-deal-field", "set-next-step"];
  if (name === "find-and-catch-up") return ["catch-me-up", "find"];
  if (name === "prepare-conversation") return ["find-and-catch-up", "who-do-we-know"];
  if (name === "morning-brief") return ["claim-card", "deal-room-board", "loop-board", "today-triage"];
  if (name === "call-verb") return ["*registered_operation"];
  return [];
}

export function mcpInventory(tools = TOOLS) {
  return Object.entries(tools).sort(([left], [right]) => left.localeCompare(right)).map(([name, tool]) => {
    const write = tool.write === true;
    const authorityOnly = tool.authorityOnly === true;
    const humanOnly = tool.humanOnly === true;
    return {
      ingress_key: `mcp-tool:${name}`,
      ingress_kind: "mcp_tool",
      operation: name,
      effect_class: name === "call-verb" ? "delegating" : write
        ? "administrative_mutation"
        : "audit_side_effect",
      source_locator: tool.registrySource,
      schema_digest: sha256(tool.inputSchema || {}),
      source_digest: sourceDigest(tool.registrySource),
      write,
      human_only: humanOnly,
      authority_only: authorityOnly,
      principal_mode: authorityOnly ? "server_verified_partner_authority" : humanOnly
        ? "server_verified_human_or_native_agent" : "authenticated_registered_principal",
      mutation_kind: write ? "scac.mutation.admin" : null,
      target_surface: "scac.surface.database",
      delegates_to: delegatesTo(name),
      request_shape: "closed_top_level_schema",
      idempotency_mode: write ? "operation_principal_manifest_bound" : "not_applicable",
      rollback_class: !write ? "append_only_audit" : "forward_fix_only",
      admission_class: "application_default_deny",
      owner_package: "11",
      implementation_state: "source_guarded_not_deployed",
      classification_authorizing: false,
    };
  });
}

const APPLICATION_INGRESSES = [
  ["worker-route:ingest", "worker_route", "mcp-server/src/index.js", "record_mutation"],
  ["worker-route:capture", "worker_route", "mcp-server/src/capture.js", "record_mutation"],
  ["worker-route:deal-room-turn", "worker_route", "mcp-server/src/index.js", "record_mutation"],
  ["worker-route:program6-routine", "worker_route", "mcp-server/src/program6-routine-controller.js", "record_mutation"],
  ["worker-route:oauth-kv", "worker_route", "mcp-server/src/google-oidc.js", "administrative_mutation"],
  ["worker-route:browser-challenge", "worker_route", "mcp-server/src/program6-browser-challenge.js", "record_mutation"],
  ["worker-sidewrite:failure-record", "worker_sidewrite", "mcp-server/src/trace.js", "audit_side_effect"],
  ["worker-sidewrite:tool-read-call", "worker_sidewrite", "mcp-server/src/mcp.js", "audit_side_effect"],
  ["worker-sidewrite:situation-retrieval", "worker_sidewrite", "mcp-server/src/situation-retrieval.js", "audit_side_effect"],
];

function sourceDigest(path) {
  return createHash("sha256").update(readFileSync(resolve(REPO_ROOT, path))).digest("hex");
}

export function parseGitIndexEntries(raw) {
  const records = Buffer.isBuffer(raw) ? raw.toString("utf8") : String(raw || "");
  return records.split("\0").filter(Boolean).flatMap(record => {
    const tab = record.indexOf("\t");
    if (tab < 0) throw new Error("git index entry missing path separator");
    const [mode, _objectId, stage] = record.slice(0, tab).split(" ");
    if (!/^(?:100644|100755)$/.test(mode) || stage !== "0") return [];
    return [{ path: record.slice(tab + 1), executable: mode === "100755" }];
  });
}

function trackedIndexEntries() {
  return parseGitIndexEntries(execFileSync("git", ["ls-files", "--stage", "-z"], {
    cwd: REPO_ROOT, encoding: "buffer",
  }));
}

export function isScriptEntrypoint(path, executable, source) {
  const base = path.split("/").at(-1);
  if (base.startsWith("test-") || base.startsWith("test_") || path.includes("/tests/")) return false;
  if (/(?:^|[-_])selftest(?:[-_.]|$)/.test(base) || path.includes("/test/")) return false;
  const knownExtension = /\.(?:py|sh|applescript|mjs|js)$/.test(path);
  if (!knownExtension && !executable) return false;
  if (source.startsWith("#!")) return true;
  if (path.endsWith(".applescript")) return true;
  if (!/\.(?:py|mjs|js)$/.test(path)) return false;
  if (path.endsWith(".py"))
    return /if\s+__name__\s*==\s*["']__main__["']\s*:/.test(source);
  return /\.(?:mjs|js)$/.test(path) &&
    (source.includes("process.argv") || source.includes("import.meta.url ===") || source.includes("require.main === module"));
}

export function discoverScriptEntrypoints(indexEntries = trackedIndexEntries(),
  sourceReader = path => readFileSync(resolve(REPO_ROOT, path), "utf8")) {
  return indexEntries.filter(({ path, executable }) =>
    isScriptEntrypoint(path, executable, sourceReader(path))).map(({ path }) => path).sort();
}

export function nonMcpInventory() {
  const application = APPLICATION_INGRESSES.map(([ingress_key, ingress_kind, source_locator, effect_class]) => ({
    ingress_key, ingress_kind, operation: ingress_key.split(":").slice(1).join(":"), effect_class,
    source_locator, schema_digest: sourceDigest(source_locator), handler_digest: sourceDigest(source_locator),
    write: true, human_only: false, authority_only: effect_class === "administrative_mutation",
    principal_mode: "existing_entrypoint_specific_authentication", mutation_kind: effect_class === "administrative_mutation" ? "scac.mutation.admin" : "scac.mutation.business_record",
    target_surface: "scac.surface.database", delegates_to: [], request_shape: "entrypoint_specific_current_contract",
    idempotency_mode: "entrypoint_specific_registered_current_control", rollback_class: "forward_fix_only",
    admission_class: "registered_inventory_only", owner_package: "11",
    implementation_state: "inventoried_not_atomically_mediated", classification_authorizing: false,
  }));
  const scripts = discoverScriptEntrypoints().map(source_locator => {
    const breakGlass = new Set(["tools/db-tap.py", "tools/call-verb.py", "tools/run-breakglass.py"]).has(source_locator);
    const genericDelegator = new Set(["run.sh", "mcp-server/local-verb.mjs"]).has(source_locator);
    const externalAdmin = !breakGlass && /(?:deploy|migrate|provision|rotate-credential|cleanup|cutoff|install-|sync-.*prod)/.test(source_locator);
    const ingress_kind = breakGlass ? "break_glass" : externalAdmin ? "external_admin" : "script_entrypoint";
    return {
    ingress_key: `${ingress_kind.replaceAll("_", "-")}:${source_locator}`, ingress_kind,
    operation: source_locator, effect_class: breakGlass ? "break_glass" : genericDelegator ? "delegating" : "administrative_mutation", source_locator,
    schema_digest: sourceDigest(source_locator), handler_digest: sourceDigest(source_locator),
    write: true, human_only: false, authority_only: true,
    principal_mode: "existing_script_specific_credential_boundary",
    mutation_kind: breakGlass ? "scac.mutation.break_glass" : "scac.mutation.admin",
    target_surface: "scac.surface.runtime",
    delegates_to: source_locator === "run.sh" ? ["*registered_script_entrypoint"]
      : source_locator === "mcp-server/local-verb.mjs" ? ["*registered_mcp_tool"] : [],
    request_shape: "fixed_script_cli_contract",
    idempotency_mode: "script_specific_registered_current_control", rollback_class: "forward_fix_only",
    admission_class: "registered_inventory_only", owner_package: "11",
    implementation_state: "inventoried_not_atomically_mediated", classification_authorizing: false,
  }});
  return [...application, ...scripts].sort((left, right) => left.ingress_key.localeCompare(right.ingress_key));
}

export function jobDefinitionInventory() {
  const manifest = JSON.parse(readFileSync(resolve(REPO_ROOT, "ops/config/control-plane-workflows.v1.json"), "utf8"));
  return manifest.workflows.map(workflow => {
    const execution = { ...workflow.execution };
    delete execution.kind;
    const projected = {
      ingress_key: `job-definition:${workflow.key}:${workflow.version}`,
      ingress_kind: "job_definition",
      key: workflow.key,
      version: workflow.version,
      enabled: workflow.enabled,
      risk: workflow.risk,
      owner_actor: workflow.inventory?.owner || "system",
      execution_kind: workflow.execution.kind,
      entrypoint: workflow.execution.entrypoint || workflow.execution.cognition_job,
      execution_contract: execution,
      inventory_contract: workflow.inventory || {},
      recurrence: workflow.recurrence,
      state_contract: workflow.state,
      routing_contract: workflow.routing,
      filtering_contract: workflow.filtering,
      validation_contract: workflow.validation,
      retry_policy: workflow.retry,
      deduplication: workflow.deduplication,
      completion_contract: workflow.completion,
      legacy_schedule: workflow.legacy_schedule,
    };
    return {
      ...projected,
      effect_class: "administrative_mutation",
      source_locator: `ops/config/control-plane-workflows.v1.json#${workflow.key}:v${workflow.version}`,
      write: true,
      human_only: false,
      authority_only: true,
      principal_mode: "existing_control_plane_authority_sync",
      mutation_kind: "scac.mutation.admin",
      target_surface: "scac.surface.runtime",
      delegates_to: [projected.entrypoint],
      request_shape: "closed_reviewed_workflow_contract",
      idempotency_mode: "job_key_version_and_schedule_bound",
      rollback_class: "forward_fix_only",
      admission_class: "registered_inventory_only",
      owner_package: "11",
      implementation_state: "inventoried_not_atomically_mediated",
      classification_authorizing: false,
    };
  }).sort((left, right) => left.ingress_key.localeCompare(right.ingress_key));
}

function yamlSections(source, key) {
  const lines = source.split(/\r?\n/);
  const sections = [];
  for (let index = 0; index < lines.length; index += 1) {
    const match = lines[index].match(new RegExp(`^(\\s*)${key}:`));
    if (!match) continue;
    const indent = match[1].length;
    const section = [lines[index]];
    for (let cursor = index + 1; cursor < lines.length; cursor += 1) {
      const line = lines[cursor];
      if (line.trim() && !line.trimStart().startsWith("#") && line.search(/\S/) <= indent) break;
      section.push(line);
    }
    sections.push(section.join("\n"));
  }
  return sections;
}

function decodeXmlText(value) {
  return value
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&quot;", '"')
    .replaceAll("&apos;", "'")
    .replaceAll("&amp;", "&")
    .replace(/&#x([0-9a-f]+);/gi, (_, hex) => String.fromCodePoint(Number.parseInt(hex, 16)))
    .replace(/&#([0-9]+);/g, (_, decimal) => String.fromCodePoint(Number.parseInt(decimal, 10)));
}

export function parsePlistXml(source) {
  const clean = source
    .replace(/<!--[\s\S]*?-->/g, "")
    .replace(/<\?xml[\s\S]*?\?>/g, "")
    .replace(/<!DOCTYPE[\s\S]*?>/g, "");
  const tokens = clean.match(/<[^>]+>|[^<]+/g) || [];
  let cursor = 0;
  const skipWhitespace = () => {
    while (cursor < tokens.length && !tokens[cursor].startsWith("<") && !tokens[cursor].trim()) cursor += 1;
  };
  const parseElement = () => {
    skipWhitespace();
    const opening = tokens[cursor++];
    const selfMatch = opening?.match(/^<([A-Za-z][A-Za-z0-9]*)(?:\s[^>]*)?\/>$/);
    if (selfMatch) {
      if (selfMatch[1] === "true") return true;
      if (selfMatch[1] === "false") return false;
      throw new Error(`unsupported self-closing plist element ${selfMatch[1]}`);
    }
    const openMatch = opening?.match(/^<([A-Za-z][A-Za-z0-9]*)(?:\s[^>]*)?>$/);
    if (!openMatch) throw new Error(`malformed plist token ${opening || "<eof>"}`);
    const tag = openMatch[1];
    if (tag === "plist") {
      const value = parseElement();
      skipWhitespace();
      if (tokens[cursor++] !== "</plist>") throw new Error("plist root is not closed");
      return value;
    }
    if (tag === "dict") {
      const value = {};
      while (true) {
        skipWhitespace();
        if (tokens[cursor] === "</dict>") { cursor += 1; return value; }
        if (!tokens[cursor]?.startsWith("<key")) throw new Error("plist dict key is missing");
        const key = parseElement();
        if (Object.hasOwn(value, key)) throw new Error(`duplicate plist key ${key}`);
        value[key] = parseElement();
      }
    }
    if (tag === "array") {
      const value = [];
      while (true) {
        skipWhitespace();
        if (tokens[cursor] === "</array>") { cursor += 1; return value; }
        value.push(parseElement());
      }
    }
    if (!["key", "string", "integer", "real", "date", "data"].includes(tag))
      throw new Error(`unsupported plist element ${tag}`);
    let raw = "";
    while (cursor < tokens.length && tokens[cursor] !== `</${tag}>`) raw += tokens[cursor++];
    if (tokens[cursor++] !== `</${tag}>`) throw new Error(`plist ${tag} is not closed`);
    const text = decodeXmlText(raw);
    if (tag === "integer") {
      if (!/^-?[0-9]+$/.test(text.trim())) throw new Error(`invalid plist integer ${text}`);
      return Number.parseInt(text.trim(), 10);
    }
    if (tag === "real") {
      const value = Number(text.trim());
      if (!Number.isFinite(value)) throw new Error(`invalid plist real ${text}`);
      return value;
    }
    return tag === "data" ? text.replace(/\s+/g, "") : text;
  };
  const value = parseElement();
  skipWhitespace();
  if (cursor !== tokens.length) throw new Error(`unexpected plist content ${tokens[cursor]}`);
  return value;
}

export function validateLaunchdAuthorityCatalogs(launchdPaths, services, legacy) {
  const reviewedPaths = new Set(launchdPaths);
  if (reviewedPaths.size !== launchdPaths.length) throw new Error("duplicate launchd source path");
  const serviceKeys = new Set();
  for (const service of services.services || []) {
    if (serviceKeys.has(service.key)) throw new Error(`duplicate ops.service key ${service.key}`);
    serviceKeys.add(service.key);
  }
  const servicesByPlist = new Map();
  for (const service of services.services || []) {
    const serviceEnvironments = new Set();
    for (const environment of service.environments || []) {
      if (serviceEnvironments.has(environment.environment))
        throw new Error(`duplicate ops.service environment ${service.key}:${environment.environment}`);
      serviceEnvironments.add(environment.environment);
      const path = environment.deploy_mechanism;
      if (typeof path !== "string" || !path.startsWith("ops/launchd/") || !path.endsWith(".plist")) continue;
      if (typeof environment.environment !== "string" || !environment.environment)
        throw new Error(`launchd ops.service environment is missing ${service.key}:${path}`);
      const mappings = servicesByPlist.get(path) || [];
      const mapping = { service_key: service.key, environment: environment.environment };
      servicesByPlist.set(path, [...mappings, mapping]
        .sort((left, right) => `${left.service_key}:${left.environment}`.localeCompare(`${right.service_key}:${right.environment}`)));
    }
  }
  const missingServices = [...reviewedPaths].filter(path => !servicesByPlist.has(path)).sort();
  const orphanServices = [...servicesByPlist.keys()].filter(path => !reviewedPaths.has(path)).sort();
  if (missingServices.length || orphanServices.length)
    throw new Error(`launchd ops.service catalog closure mismatch missing=${missingServices.join(",")} orphan=${orphanServices.join(",")}`);

  const legacyByPlist = new Map();
  const legacySurfaceIds = new Set();
  for (const surface of legacy.surfaces || []) {
    if (surface.scheduler_kind !== "launchd") continue;
    if (legacySurfaceIds.has(surface.surface_id)) throw new Error(`duplicate launchd legacy surface ${surface.surface_id}`);
    legacySurfaceIds.add(surface.surface_id);
    if (!reviewedPaths.has(surface.repo_plist_relpath))
      throw new Error(`orphan launchd legacy path ${surface.repo_plist_relpath}`);
    if (legacyByPlist.has(surface.repo_plist_relpath))
      throw new Error(`duplicate launchd legacy path ${surface.repo_plist_relpath}`);
    legacyByPlist.set(surface.repo_plist_relpath, surface);
  }
  return { servicesByPlist, legacyByPlist };
}

export function assertLegacyLaunchdSource(surface, sourceLocator, plist) {
  if (surface.repo_plist_relpath !== sourceLocator || plist.Label !== surface.locator ||
      JSON.stringify(plist.ProgramArguments) !== JSON.stringify(surface.canonical_program_arguments) ||
      sha256(plist) !== surface.canonical_plist_fingerprint)
    throw new Error(`launchd legacy source mismatch ${surface.surface_id}`);
}

function launchdAuthorityMaps(launchdPaths) {
  const servicesPath = "ops/config/services.json";
  const legacyPath = "ops/config/control-plane-scheduler-cutover.v1.json";
  const services = JSON.parse(readFileSync(resolve(REPO_ROOT, servicesPath), "utf8"));
  const legacy = JSON.parse(readFileSync(resolve(REPO_ROOT, legacyPath), "utf8"));
  const { servicesByPlist, legacyByPlist } = validateLaunchdAuthorityCatalogs(launchdPaths, services, legacy);
  return {
    servicesByPlist,
    legacyByPlist,
    catalogDigests: {
      services: `sha256:${sourceDigest(servicesPath)}`,
      legacy_schedule_launchd: `sha256:${sourceDigest(legacyPath)}`,
    },
  };
}

export function workflowDefinitionInventory() {
  const trackedPaths = trackedIndexEntries().map(entry => entry.path);
  const github = trackedPaths.filter(path => path.startsWith(".github/workflows/") && /\.ya?ml$/.test(path))
    .sort().map(source_locator => {
    const source = readFileSync(resolve(REPO_ROOT, source_locator), "utf8");
    const runSource = yamlSections(source, "run").join("\n");
    const actionDelegates = [...source.matchAll(/^\s*-?\s*uses:\s*([^\s#]+)/gm)]
      .map(match => `github-action:${match[1]}`);
    const scriptDelegates = [...runSource.matchAll(/(?:^|[\s"'])(?:(?:\.\/)?((?:bin|tools|ops|pipelines|hooks)\/[A-Za-z0-9_./-]+))/gm)]
      .map(match => `script:${match[1]}`);
    const commandDelegates = [
      ...(runSource.includes("aws s3api put-object") ? ["shell:aws-s3api-put-object"] : []),
      ...(runSource.includes("gh pr merge") ? ["shell:gh-pr-merge"] : []),
    ];
    return {
      ingress_key: `github-workflow:${source_locator.split("/").at(-1)}`,
      ingress_kind: "workflow_entrypoint",
      operation: source_locator,
      effect_class: "administrative_mutation",
      source_locator,
      schema_digest: sourceDigest(source_locator),
      handler_digest: sourceDigest(source_locator),
      trigger_contract_digest: sha256(yamlSections(source, "on")),
      permissions_contract_digest: sha256(yamlSections(source, "permissions")),
      write: true,
      human_only: false,
      authority_only: true,
      principal_mode: "github_actions_token_and_declared_workflow_permissions",
      mutation_kind: "scac.mutation.admin",
      target_surface: "scac.surface.runtime",
      delegates_to: [...new Set([...actionDelegates, ...scriptDelegates, ...commandDelegates])].sort(),
      request_shape: "github_workflow_trigger_and_permissions_source_bound",
      idempotency_mode: "github_run_and_attempt_bound_current_control",
      rollback_class: "forward_fix_only",
      admission_class: "registered_inventory_only",
      owner_package: "11",
      implementation_state: "inventoried_not_atomically_mediated",
      classification_authorizing: false,
    };
  });
  const scriptEntrypoints = new Set(discoverScriptEntrypoints());
  const launchdPaths = trackedPaths.filter(path => path.startsWith("ops/launchd/") && path.endsWith(".plist")).sort();
  const { servicesByPlist, legacyByPlist, catalogDigests } = launchdAuthorityMaps(launchdPaths);
  const launchd = launchdPaths.map(source_locator => {
    const source = readFileSync(resolve(REPO_ROOT, source_locator), "utf8");
    const plist = parsePlistXml(source);
    const programArguments = Array.isArray(plist.ProgramArguments) ? plist.ProgramArguments : [];
    const label = typeof plist.Label === "string" ? plist.Label : null;
    const delegates = programArguments.filter(argument => argument.includes("/")).map(argument => {
      let candidate = argument;
      if (candidate.startsWith("{{REPO}}/")) candidate = candidate.slice("{{REPO}}/".length);
      else if (candidate.startsWith("/Users/booko/carr-system/")) candidate = candidate.slice("/Users/booko/carr-system/".length);
      if (scriptEntrypoints.has(candidate)) return `script:${candidate}`;
      return argument.startsWith("/") ? `executable:${argument}` : `argument:${argument}`;
    });
    const triggerContract = Object.fromEntries(["KeepAlive", "RunAtLoad", "StartInterval", "StartCalendarInterval", "WatchPaths", "QueueDirectories"]
      .filter(key => Object.hasOwn(plist, key)).map(key => [key, plist[key]]));
    const environmentContract = plist.EnvironmentVariables || {};
    const serviceMappings = servicesByPlist.get(source_locator) || [];
    if (!serviceMappings.length) throw new Error(`launchd workflow lacks ops.service authority mapping: ${source_locator}`);
    const physicalAuthorityRefs = serviceMappings.map(mapping =>
      `ops.service_environment:${mapping.service_key}:${mapping.environment}`);
    const legacySurface = legacyByPlist.get(source_locator);
    if (legacySurface) {
      assertLegacyLaunchdSource(legacySurface, source_locator, plist);
      physicalAuthorityRefs.push(`ops.legacy_schedule_launchd_contract:${legacySurface.surface_id}`);
    }
    return {
      ingress_key: `launchd-workflow:${label || source_locator.split("/").at(-1)}`,
      ingress_kind: "workflow_entrypoint",
      operation: source_locator,
      effect_class: "administrative_mutation",
      source_locator,
      schema_digest: sourceDigest(source_locator),
      handler_digest: sourceDigest(source_locator),
      launchd_label: label,
      trigger_contract_digest: sha256(triggerContract),
      environment_contract_digest: sha256(environmentContract),
      program_arguments_digest: sha256(programArguments),
      write: true,
      human_only: false,
      authority_only: true,
      principal_mode: "launchd_user_session_and_declared_environment",
      mutation_kind: "scac.mutation.admin",
      target_surface: "scac.surface.runtime",
      delegates_to: [...new Set(delegates)].sort(),
      request_shape: "launchd_trigger_arguments_environment_source_bound",
      idempotency_mode: "delegated_entrypoint_current_control",
      rollback_class: "forward_fix_only",
      admission_class: "registered_inventory_only",
      physical_authority_refs: physicalAuthorityRefs.sort(),
      physical_authority_catalog_digests: catalogDigests,
      owner_package: "11",
      implementation_state: "inventoried_not_atomically_mediated",
      classification_authorizing: false,
    };
  });
  return [...github, ...launchd].sort((left, right) => left.ingress_key.localeCompare(right.ingress_key));
}

export function fullInventory(tools = TOOLS) {
  return [...mcpInventory(tools), ...nonMcpInventory(), ...jobDefinitionInventory(), ...workflowDefinitionInventory()]
    .sort((left, right) => left.ingress_key.localeCompare(right.ingress_key));
}

export function registryDigestFor(version, rows = fullInventory(), dbCatalogBaseline = DB_CATALOG_BASELINE) {
  if (![REGISTRY_VERSION, REGISTRY_V2_VERSION, REGISTRY_V3_VERSION, REGISTRY_V4_VERSION,
    REGISTRY_V5_VERSION, REGISTRY_V6_VERSION, REGISTRY_V7_VERSION, REGISTRY_V8_VERSION,
    REGISTRY_V9_VERSION, REGISTRY_V10_VERSION].includes(version))
    throw new Error(`unsupported SCAC mutation registry version: ${version}`);
  return sha256({ schema_version: version, rows, db_catalog_baseline: dbCatalogBaseline });
}

export function registryDigest(rows = fullInventory()) {
  return registryDigestFor(REGISTRY_VERSION, rows, DB_CATALOG_BASELINE);
}

export function sourceContractSetDigest(rows = fullInventory()) {
  return sha256(rows.map(row => `sha256:${sha256(row)}`).sort().join(","));
}

export function registrySeal(version, rows, dbCatalogBaseline) {
  const catalogEntryCount = dbCatalogBaseline.secdef_execute.count +
    dbCatalogBaseline.relation_dml.count + dbCatalogBaseline.column_dml.count;
  return Object.freeze({
    version,
    digest: `sha256:${registryDigestFor(version, rows, dbCatalogBaseline)}`,
    entryCount: rows.length + catalogEntryCount,
    sourceEntryCount: rows.length,
  });
}

export function renderRuntimeProjection(rows = fullInventory(), {
  version = REGISTRY_VERSION,
  dbCatalogBaseline = DB_CATALOG_BASELINE,
} = {}) {
  const digest = registryDigestFor(version, rows, dbCatalogBaseline);
  const sourceSetDigest = sourceContractSetDigest(rows);
  const catalogBaselineDigest = sha256(dbCatalogBaseline);
  const projection = Object.fromEntries(rows.filter(row => row.ingress_kind === "mcp_tool").map(row => [row.operation, {
    ingress_key: row.ingress_key,
    source_locator: row.source_locator,
    source_digest: row.source_digest,
    schema_digest: row.schema_digest,
    write: row.write,
    human_only: row.human_only,
    authority_only: row.authority_only,
    delegates_to: row.delegates_to,
  }]));
  return `// GENERATED by ops/scac-mutation-inventory.mjs. Review changes; never hand-edit.\n` +
    `// This is a non-authorizing source/build guard. The sealed DB registry is SIEP-11's sole metadata authority; SIEP-18 owns atomic admission.\n` +
    `export const SCAC_MUTATION_REGISTRY_VERSION = ${JSON.stringify(version)};\n` +
    `export const SCAC_MUTATION_REGISTRY_DIGEST = ${JSON.stringify(digest)};\n` +
    `export const SCAC_MUTATION_SOURCE_CONTRACT_SET_DIGEST = ${JSON.stringify(sourceSetDigest)};\n` +
    `export const SCAC_MUTATION_DB_CATALOG_BASELINE_DIGEST = ${JSON.stringify(catalogBaselineDigest)};\n` +
    `export const SCAC_MUTATION_DB_METADATA_AUTHORITY = true;\n` +
    `export const SCAC_MUTATION_RUNTIME_PROJECTION_AUTHORIZING = false;\n` +
    `export const SCAC_MUTATION_OPERATIONS = Object.freeze(${JSON.stringify(projection, null, 2)});\n`;
}

function sqlLiteral(value) {
  return `'${String(value).replaceAll("'", "''")}'`;
}

function catalogSeedSql(version = REGISTRY_VERSION) {
  const common = `'effect_class','administrative_mutation','owner_package','11',` +
    `'implementation_state','inventoried_not_atomically_mediated','classification_authorizing',false`;
  return `-- Exact database capability rows are projected from the just-built catalog.\n` +
`-- The sealed expected category digests are independently recomputed by the DB gate.\n` +
`with recursive connected(oid) as (\n` +
`  select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union\n` +
`  select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci' and not other.rolsuper\n` +
`), runtime_roles as (select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper\n` +
`), functions as (\n` +
`  select p.oid,n.nspname,p.proname,pg_get_function_identity_arguments(p.oid) args,p.prosecdef,p.prokind,p.provolatile,p.proparallel,p.proconfig,p.proacl,p.proowner\n` +
`    from pg_proc p join pg_namespace n on n.oid=p.pronamespace\n` +
`   where n.nspname not in ('pg_catalog','information_schema') and p.prokind in ('f','p')\n` +
`), capabilities as (\n` +
`  select f.*,acl.grantee,acl.privilege_type,acl.is_grantable from functions f\n` +
`  cross join lateral aclexplode(coalesce(f.proacl,acldefault('f',f.proowner))) acl\n` +
`), observed as (\n` +
`  select jsonb_build_object('ingress_key','db-function-acl:'||nspname||'.'||proname||'('||args||'):'||coalesce(r.rolname,'public')||':execute',\n` +
`    'ingress_kind','db_function_acl','signature',nspname||'.'||proname||'('||args||')','security_definer',prosecdef,\n` +
`    'function_kind',prokind,'volatility',provolatile,'parallel',proparallel,'config',coalesce(to_jsonb(proconfig),'[]'::jsonb),\n` +
`    'grantee',coalesce(r.rolname,'public'),'privilege','execute','grantable',is_grantable) row\n` +
`  from capabilities c left join pg_roles r on r.oid=c.grantee\n` +
`  where prosecdef and privilege_type='EXECUTE' and grantee<>proowner and (grantee=0 or r.oid in(select oid from runtime_roles))\n` +
`), contracts as (\n` +
`  select row||jsonb_build_object(${common},'source_locator',row->>'signature') contract from observed\n` +
`)\n` +
`insert into ops.scac_mutation_registry_entry(registry_version,ingress_key,ingress_kind,effect_class,source_locator,entry_digest,contract)\n` +
`select ${sqlLiteral(version)},contract->>'ingress_key',contract->>'ingress_kind',contract->>'effect_class',contract->>'source_locator',\n` +
`  'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(contract),'UTF8'),'sha256'),'hex'),contract from contracts;\n\n` +
`with recursive connected(oid) as (select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci' and not other.rolsuper), runtime_roles as (select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper), capabilities as (\n` +
`  select n.nspname,c.relname,c.relkind,c.relowner,acl.grantee,acl.privilege_type,acl.is_grantable\n` +
`  from pg_class c join pg_namespace n on n.oid=c.relnamespace\n` +
`  cross join lateral aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) acl\n` +
`  where n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f')\n` +
`), observed as (\n` +
`  select jsonb_build_object('ingress_key','db-relation-acl:'||nspname||'.'||relname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type),\n` +
`    'ingress_kind','db_relation_acl','relation',nspname||'.'||relname,'relation_kind',relkind,'grantee',coalesce(r.rolname,'public'),\n` +
`    'privilege',lower(privilege_type),'grantable',is_grantable) row\n` +
`  from capabilities c left join pg_roles r on r.oid=c.grantee\n` +
`  where privilege_type in ('INSERT','UPDATE','DELETE','TRUNCATE') and grantee<>relowner and (grantee=0 or r.oid in(select oid from runtime_roles))\n` +
`), contracts as (\n` +
`  select row||jsonb_build_object(${common},'source_locator',row->>'relation') contract from observed\n` +
`)\n` +
`insert into ops.scac_mutation_registry_entry(registry_version,ingress_key,ingress_kind,effect_class,source_locator,entry_digest,contract)\n` +
`select ${sqlLiteral(version)},contract->>'ingress_key',contract->>'ingress_kind',contract->>'effect_class',contract->>'source_locator',\n` +
`  'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(contract),'UTF8'),'sha256'),'hex'),contract from contracts;\n\n` +
`with recursive connected(oid) as (select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci' and not other.rolsuper), runtime_roles as (select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper), capabilities as (\n` +
`  select n.nspname,c.relname,c.relkind,c.relowner,a.attname,acl.grantee,acl.privilege_type,acl.is_grantable\n` +
`  from pg_attribute a join pg_class c on c.oid=a.attrelid join pg_namespace n on n.oid=c.relnamespace\n` +
`  cross join lateral aclexplode(a.attacl) acl\n` +
`  where a.attnum>0 and not a.attisdropped and a.attacl is not null and cardinality(a.attacl)>0\n` +
`    and n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f')\n` +
`), observed as (\n` +
`  select jsonb_build_object('ingress_key','db-column-acl:'||nspname||'.'||relname||'.'||attname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type),\n` +
`    'ingress_kind','db_column_acl','relation',nspname||'.'||relname,'relation_kind',relkind,'column',attname,\n` +
`    'grantee',coalesce(r.rolname,'public'),'privilege',lower(privilege_type),'grantable',is_grantable) row\n` +
`  from capabilities c left join pg_roles r on r.oid=c.grantee\n` +
`  where privilege_type in ('INSERT','UPDATE') and grantee<>relowner and (grantee=0 or r.oid in(select oid from runtime_roles))\n` +
`), contracts as (\n` +
`  select row||jsonb_build_object(${common},'source_locator',(row->>'relation')||'.'||(row->>'column')) contract from observed\n` +
`)\n` +
`insert into ops.scac_mutation_registry_entry(registry_version,ingress_key,ingress_kind,effect_class,source_locator,entry_digest,contract)\n` +
`select ${sqlLiteral(version)},contract->>'ingress_key',contract->>'ingress_kind',contract->>'effect_class',contract->>'source_locator',\n` +
`  'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(contract),'UTF8'),'sha256'),'hex'),contract from contracts;\n\n`;
}

function renderSuccessorRegistrySql(rows = fullInventory(),
  dbCatalogBaseline = SIEP12_DB_CATALOG_BASELINE) {
  const version = REGISTRY_V2_VERSION;
  const predecessor = registrySeal(REGISTRY_VERSION, rows, DB_CATALOG_BASELINE);
  const digest = registryDigestFor(version, rows, dbCatalogBaseline);
  const catalogCount = dbCatalogBaseline.secdef_execute.count +
    dbCatalogBaseline.relation_dml.count + dbCatalogBaseline.column_dml.count;
  const totalCount = rows.length + catalogCount;
  const seed = JSON.stringify(rows.map(row => ({ ...row, entry_digest: `sha256:${sha256(row)}` })));
  return `-- BEGIN GENERATED SIEP-12 REGISTRY V2; never hand-edit.\n` +
`drop trigger scac_mutation_registry_version_sealed on ops.scac_mutation_registry_version;\n` +
`drop trigger scac_mutation_registry_entry_sealed on ops.scac_mutation_registry_entry;\n` +
`alter table ops.scac_mutation_registry_version drop constraint scac_mutation_registry_version_registry_version_check;\n` +
`alter table ops.scac_mutation_registry_version add constraint scac_mutation_registry_version_registry_version_check\n` +
`  check (registry_version in ('scac-mutation-registry.v1','scac-mutation-registry.v2'));\n\n` +
`insert into ops.scac_mutation_registry_version(registry_version,program_key,package_key,charter_digest,registry_digest,entry_count,source_entry_count,catalog_projection,entry_set_digest,mcp_default_deny_source_guarded,db_metadata_authority,runtime_projection_authorizing,non_mcp_default_deny_operational,atomic_database_mediation_operational,direct_database_grant_cutover,production_enforcement_active)\n` +
`values ('${version}','carr-system-integrity-elimination-v1','11','sha256:473b7b1cd2ea975ba118f05406b35f4affdda0cb61f4487c252db129a882151c','sha256:${digest}',${totalCount},${rows.length},${sqlLiteral(JSON.stringify(dbCatalogBaseline))}::jsonb,'sha256:${"0".repeat(64)}',true,true,false,false,false,false,false);\n\n` +
`with seed as (select value as contract from jsonb_array_elements(${sqlLiteral(seed)}::jsonb))\n` +
`insert into ops.scac_mutation_registry_entry(registry_version,ingress_key,ingress_kind,effect_class,source_locator,entry_digest,contract)\n` +
`select '${version}',contract->>'ingress_key',contract->>'ingress_kind',contract->>'effect_class',contract->>'source_locator',\n` +
`       'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(contract-'entry_digest'),'UTF8'),'sha256'),'hex'),contract-'entry_digest' from seed;\n\n` +
catalogSeedSql(version) +
`do $$ declare actual_count integer; actual_digest text; expected jsonb; category text; kind text;\n` +
`begin\n` +
`  for category,kind in values ('secdef_execute','db_function_acl'),('relation_dml','db_relation_acl'),('column_dml','db_column_acl') loop\n` +
`    select count(*),'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(jsonb_agg(contract-'effect_class'-'owner_package'-'implementation_state'-'classification_authorizing'-'source_locator' order by ingress_key collate "C", ops.scac_canonical_json(contract-'effect_class'-'owner_package'-'implementation_state'-'classification_authorizing'-'source_locator') collate "C"),'[]'::jsonb)),'UTF8'),'sha256'),'hex')\n` +
`      into actual_count,actual_digest from ops.scac_mutation_registry_entry where registry_version='${version}' and ingress_kind=kind;\n` +
`    select catalog_projection->category into expected from ops.scac_mutation_registry_version where registry_version='${version}';\n` +
`    if actual_count<>(expected->>'count')::integer or actual_digest<>expected->>'digest' then raise exception 'SCAC v2 database catalog category % drifted: count %, digest %',category,actual_count,actual_digest; end if;\n` +
`  end loop;\n` +
`end $$;\n\n` +
`update ops.scac_mutation_registry_version v set entry_set_digest=(select 'sha256:'||encode(public.digest(convert_to(string_agg(e.entry_digest,',' order by e.ingress_key collate "C"),'UTF8'),'sha256'),'hex') from ops.scac_mutation_registry_entry e where e.registry_version=v.registry_version) where registry_version='${version}';\n` +
`do $$ begin\n` +
`  if (select count(*) from ops.scac_mutation_registry_entry where registry_version='${version}')<>${totalCount}\n` +
`     or exists(select 1 from ops.scac_mutation_registry_entry where registry_version='${version}' and (contract->>'owner_package'<>'11' or (contract->>'classification_authorizing')::boolean))\n` +
`     or exists(select 1 from ops.scac_mutation_registry_entry where registry_version='${version}' and entry_digest is distinct from 'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(contract),'UTF8'),'sha256'),'hex')) then raise exception 'SCAC mutation registry v2 seed is incomplete or drifted'; end if;\n` +
`  if (select registry_digest from ops.scac_mutation_registry_version where registry_version='${predecessor.version}')<>'${predecessor.digest}'\n` +
`     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='${predecessor.version}')<>${predecessor.entryCount} then raise exception 'sealed SCAC mutation registry v1 changed during successor creation'; end if;\n` +
`end $$;\n` +
`create trigger scac_mutation_registry_version_sealed before insert or update or delete on ops.scac_mutation_registry_version for each row execute function ops.scac_mutation_registry_append_only();\n` +
`create trigger scac_mutation_registry_entry_sealed before insert or update or delete on ops.scac_mutation_registry_entry for each row execute function ops.scac_mutation_registry_append_only();\n` +
`-- END GENERATED SIEP-12 REGISTRY V2.\n`;
}

function renderSIEP13RegistrySql(rows = fullInventory(),
  dbCatalogBaseline = SIEP13_DB_CATALOG_BASELINE) {
  const version = REGISTRY_V3_VERSION;
  const predecessor = registrySeal(REGISTRY_V2_VERSION, rows, SIEP12_DB_CATALOG_BASELINE);
  const digest = registryDigestFor(version, rows, dbCatalogBaseline);
  const catalogCount = dbCatalogBaseline.secdef_execute.count +
    dbCatalogBaseline.relation_dml.count + dbCatalogBaseline.column_dml.count;
  const totalCount = rows.length + catalogCount;
  const seed = JSON.stringify(rows.map(row => ({ ...row, entry_digest: `sha256:${sha256(row)}` })));
  return `-- SIEP-13 / SCAC-03: forward-only mutation registry v3.\n` +
`-- GENERATED by ops/scac-mutation-inventory.mjs; never hand-edit.\n` +
`-- Source/test implementation only; Production application remains Joe-gated.\n\n` +
`drop trigger scac_mutation_registry_version_sealed on ops.scac_mutation_registry_version;\n` +
`drop trigger scac_mutation_registry_entry_sealed on ops.scac_mutation_registry_entry;\n` +
`alter table ops.scac_mutation_registry_version drop constraint scac_mutation_registry_version_registry_version_check;\n` +
`alter table ops.scac_mutation_registry_version add constraint scac_mutation_registry_version_registry_version_check\n` +
`  check (registry_version in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3'));\n\n` +
`create or replace function ops.scac_mutation_registration_v3(p_expected_digest text,p_ingress_key text)\n` +
`returns jsonb language plpgsql stable security definer set search_path=pg_catalog,ops as $fn$\n` +
`declare v ops.scac_mutation_registry_version%rowtype; e ops.scac_mutation_registry_entry%rowtype; actual_count integer; actual_set text; bad_hash boolean;\n` +
`begin\n` +
`  select * into v from ops.scac_mutation_registry_version where registry_version='${version}';\n` +
`  if v.registry_version is null then return jsonb_build_object('registered',false,'reason','registry_unavailable'); end if;\n` +
`  select count(*),'sha256:'||encode(public.digest(convert_to(coalesce(string_agg(entry_digest,',' order by ingress_key collate "C", entry_digest collate "C"),''),'UTF8'),'sha256'),'hex'),\n` +
`         coalesce(bool_or(entry_digest is distinct from 'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(contract),'UTF8'),'sha256'),'hex')),false)\n` +
`    into actual_count,actual_set,bad_hash from ops.scac_mutation_registry_entry where registry_version=v.registry_version;\n` +
`  if actual_count<>v.entry_count or actual_set is distinct from v.entry_set_digest or bad_hash then\n` +
`    return jsonb_build_object('registered',false,'reason','registry_corrupt','registry_version',v.registry_version,'registry_digest',v.registry_digest); end if;\n` +
`  if p_expected_digest is distinct from v.registry_digest then return jsonb_build_object('registered',false,'reason','digest_mismatch','registry_version',v.registry_version,'registry_digest',v.registry_digest); end if;\n` +
`  if p_ingress_key is null or p_ingress_key !~ '^[a-z][a-z0-9_-]+:' or p_ingress_key ~ E'[\\n\\r\\t]' or char_length(p_ingress_key)>1000 then return jsonb_build_object('registered',false,'reason','malformed_ingress','registry_version',v.registry_version,'registry_digest',v.registry_digest); end if;\n` +
`  select * into e from ops.scac_mutation_registry_entry where registry_version=v.registry_version and ingress_key=p_ingress_key;\n` +
`  if e.ingress_key is null then return jsonb_build_object('registered',false,'reason','unknown_ingress','registry_version',v.registry_version,'registry_digest',v.registry_digest); end if;\n` +
`  return jsonb_build_object('registered',true,'reason','registered_inventory','registry_version',v.registry_version,'registry_digest',v.registry_digest,\n` +
`    'ingress_key',e.ingress_key,'ingress_kind',e.ingress_kind,'effect_class',e.effect_class,'entry_digest',e.entry_digest,\n` +
`    'implementation_state',e.contract->>'implementation_state','atomic_database_mediation_operational',false);\n` +
`end $fn$;\n` +
`revoke all on function ops.scac_mutation_registration_v3(text,text) from public,carr_reader,carr_writer,carr_jobs,carr_authority;\n` +
`grant execute on function ops.scac_mutation_registration_v3(text,text) to carr_reader,carr_writer,carr_jobs,carr_authority;\n` +
`comment on function ops.scac_mutation_registration_v3(text,text) is 'Read-only current SIEP mutation registry lookup; never mutation authority or artifact/root trust.';\n\n` +
`insert into ops.scac_mutation_registry_version(registry_version,program_key,package_key,charter_digest,registry_digest,entry_count,source_entry_count,catalog_projection,entry_set_digest,mcp_default_deny_source_guarded,db_metadata_authority,runtime_projection_authorizing,non_mcp_default_deny_operational,atomic_database_mediation_operational,direct_database_grant_cutover,production_enforcement_active)\n` +
`values ('${version}','carr-system-integrity-elimination-v1','11','sha256:473b7b1cd2ea975ba118f05406b35f4affdda0cb61f4487c252db129a882151c','sha256:${digest}',${totalCount},${rows.length},${sqlLiteral(JSON.stringify(dbCatalogBaseline))}::jsonb,'sha256:${"0".repeat(64)}',true,true,false,false,false,false,false);\n\n` +
`with seed as (select value as contract from jsonb_array_elements(${sqlLiteral(seed)}::jsonb))\n` +
`insert into ops.scac_mutation_registry_entry(registry_version,ingress_key,ingress_kind,effect_class,source_locator,entry_digest,contract)\n` +
`select '${version}',contract->>'ingress_key',contract->>'ingress_kind',contract->>'effect_class',contract->>'source_locator',\n` +
`  'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(contract-'entry_digest'),'UTF8'),'sha256'),'hex'),contract-'entry_digest' from seed;\n\n` +
catalogSeedSql(version) +
`do $$ declare actual_count integer; actual_digest text; expected jsonb; category text; kind text;\n` +
`begin\n` +
`  for category,kind in values ('secdef_execute','db_function_acl'),('relation_dml','db_relation_acl'),('column_dml','db_column_acl') loop\n` +
`    select count(*),'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(jsonb_agg(contract-'effect_class'-'owner_package'-'implementation_state'-'classification_authorizing'-'source_locator' order by ingress_key collate "C", ops.scac_canonical_json(contract-'effect_class'-'owner_package'-'implementation_state'-'classification_authorizing'-'source_locator') collate "C"),'[]'::jsonb)),'UTF8'),'sha256'),'hex')\n` +
`      into actual_count,actual_digest from ops.scac_mutation_registry_entry where registry_version='${version}' and ingress_kind=kind;\n` +
`    select catalog_projection->category into expected from ops.scac_mutation_registry_version where registry_version='${version}';\n` +
`    if actual_count<>(expected->>'count')::integer or actual_digest<>expected->>'digest' then raise exception 'SCAC v3 database catalog category % drifted: count %, digest %',category,actual_count,actual_digest; end if;\n` +
`  end loop;\n` +
`end $$;\n\n` +
`update ops.scac_mutation_registry_version v set entry_set_digest=(select 'sha256:'||encode(public.digest(convert_to(string_agg(e.entry_digest,',' order by e.ingress_key collate "C"),'UTF8'),'sha256'),'hex') from ops.scac_mutation_registry_entry e where e.registry_version=v.registry_version) where registry_version='${version}';\n` +
`do $$ begin\n` +
`  if (select count(*) from ops.scac_mutation_registry_entry where registry_version='${version}')<>${totalCount}\n` +
`     or exists(select 1 from ops.scac_mutation_registry_entry where registry_version='${version}' and (contract->>'owner_package'<>'11' or (contract->>'classification_authorizing')::boolean))\n` +
`     or exists(select 1 from ops.scac_mutation_registry_entry where registry_version='${version}' and entry_digest is distinct from 'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(contract),'UTF8'),'sha256'),'hex')) then raise exception 'SCAC mutation registry v3 seed is incomplete or drifted'; end if;\n` +
`  if (select registry_digest from ops.scac_mutation_registry_version where registry_version='${predecessor.version}')<>'${predecessor.digest}'\n` +
`     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='${predecessor.version}')<>${predecessor.entryCount} then raise exception 'sealed SCAC mutation registry v2 changed during successor creation'; end if;\n` +
`end $$;\n` +
`-- Retain v2 as an exact historical lookup after the live catalog advances.\n` +
`create or replace function ops.scac_mutation_catalog_v2_current()\n` +
`returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$\n` +
`  select exists(select 1 from ops.scac_mutation_registry_version\n` +
`    where registry_version='${predecessor.version}'\n` +
`      and registry_digest='${predecessor.digest}'\n` +
`      and entry_count=${predecessor.entryCount} and source_entry_count=${predecessor.sourceEntryCount})\n` +
`$fn$;\n` +
`comment on function ops.scac_mutation_catalog_v2_current() is 'Historical v2 seal availability after v3; it is not a claim that the live catalog remains v2.';\n\n` +
`create or replace function ops.scac_mutation_catalog_v3_current()\n` +
`returns boolean language plpgsql stable security definer set search_path=pg_catalog,public,ops as $fn$\n` +
`declare observed_count integer; observed_digest text;\n` +
`begin\n` +
`  with recursive connected(oid) as (select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci' and not other.rolsuper),\n` +
`  runtime_roles as (select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper),\n` +
`  functions as (select p.oid,n.nspname,p.proname,pg_get_function_identity_arguments(p.oid) args,p.prosecdef,p.prokind,p.provolatile,p.proparallel,p.proconfig,p.proacl,p.proowner from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname not in ('pg_catalog','information_schema') and p.prokind in ('f','p')),\n` +
`  capabilities as (select f.*,acl.grantee,acl.privilege_type,acl.is_grantable from functions f cross join lateral aclexplode(coalesce(f.proacl,acldefault('f',f.proowner))) acl),\n` +
`  observed as (select 'db-function-acl:'||nspname||'.'||proname||'('||args||'):'||coalesce(r.rolname,'public')||':execute' ingress_key,jsonb_build_object('ingress_key','db-function-acl:'||nspname||'.'||proname||'('||args||'):'||coalesce(r.rolname,'public')||':execute','ingress_kind','db_function_acl','signature',nspname||'.'||proname||'('||args||')','security_definer',prosecdef,'function_kind',prokind,'volatility',provolatile,'parallel',proparallel,'config',coalesce(to_jsonb(proconfig),'[]'::jsonb),'grantee',coalesce(r.rolname,'public'),'privilege','execute','grantable',is_grantable) row from capabilities c left join pg_roles r on r.oid=c.grantee where prosecdef and privilege_type='EXECUTE' and grantee<>proowner and (grantee=0 or r.oid in(select oid from runtime_roles)))\n` +
`  select count(*),'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(jsonb_agg(row order by ingress_key collate "C", ops.scac_canonical_json(row) collate "C"),'[]'::jsonb)),'UTF8'),'sha256'),'hex') into observed_count,observed_digest from observed;\n` +
`  if observed_count<>${dbCatalogBaseline.secdef_execute.count} or observed_digest<>'${dbCatalogBaseline.secdef_execute.digest}' then return false; end if;\n` +
`  with recursive connected(oid) as (select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci' and not other.rolsuper), runtime_roles as (select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper), capabilities as (select n.nspname,c.relname,c.relkind,c.relowner,acl.grantee,acl.privilege_type,acl.is_grantable from pg_class c join pg_namespace n on n.oid=c.relnamespace cross join lateral aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) acl where n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f')), observed as (select 'db-relation-acl:'||nspname||'.'||relname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type) ingress_key,jsonb_build_object('ingress_key','db-relation-acl:'||nspname||'.'||relname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type),'ingress_kind','db_relation_acl','relation',nspname||'.'||relname,'relation_kind',relkind,'grantee',coalesce(r.rolname,'public'),'privilege',lower(privilege_type),'grantable',is_grantable) row from capabilities c left join pg_roles r on r.oid=c.grantee where privilege_type in ('INSERT','UPDATE','DELETE','TRUNCATE') and grantee<>relowner and (grantee=0 or r.oid in(select oid from runtime_roles)))\n` +
`  select count(*),'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(jsonb_agg(row order by ingress_key collate "C", ops.scac_canonical_json(row) collate "C"),'[]'::jsonb)),'UTF8'),'sha256'),'hex') into observed_count,observed_digest from observed;\n` +
`  if observed_count<>${dbCatalogBaseline.relation_dml.count} or observed_digest<>'${dbCatalogBaseline.relation_dml.digest}' then return false; end if;\n` +
`  with recursive connected(oid) as (select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci' and not other.rolsuper), runtime_roles as (select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper), capabilities as (select n.nspname,c.relname,c.relkind,c.relowner,a.attname,acl.grantee,acl.privilege_type,acl.is_grantable from pg_attribute a join pg_class c on c.oid=a.attrelid join pg_namespace n on n.oid=c.relnamespace cross join lateral aclexplode(a.attacl) acl where a.attnum>0 and not a.attisdropped and a.attacl is not null and cardinality(a.attacl)>0 and n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f')), observed as (select 'db-column-acl:'||nspname||'.'||relname||'.'||attname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type) ingress_key,jsonb_build_object('ingress_key','db-column-acl:'||nspname||'.'||relname||'.'||attname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type),'ingress_kind','db_column_acl','relation',nspname||'.'||relname,'relation_kind',relkind,'column',attname,'grantee',coalesce(r.rolname,'public'),'privilege',lower(privilege_type),'grantable',is_grantable) row from capabilities c left join pg_roles r on r.oid=c.grantee where privilege_type in ('INSERT','UPDATE') and grantee<>relowner and (grantee=0 or r.oid in(select oid from runtime_roles)))\n` +
`  select count(*),'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(jsonb_agg(row order by ingress_key collate "C", ops.scac_canonical_json(row) collate "C"),'[]'::jsonb)),'UTF8'),'sha256'),'hex') into observed_count,observed_digest from observed;\n` +
`  if observed_count<>${dbCatalogBaseline.column_dml.count} or observed_digest<>'${dbCatalogBaseline.column_dml.digest}' then return false; end if;\n` +
`  with recursive connected(oid) as (\n` +
`    select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union\n` +
`    select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci'\n` +
`  ), role_rows as (\n` +
`    select 'db-role:'||r.rolname ingress_key,jsonb_build_object('ingress_key','db-role:'||r.rolname,'row_kind','role','role',r.rolname,'login',r.rolcanlogin,'inherit',r.rolinherit,'superuser',r.rolsuper,'create_role',r.rolcreaterole,'create_db',r.rolcreatedb,'replication',r.rolreplication,'bypass_rls',r.rolbypassrls) row from pg_roles r where r.oid in(select oid from connected)\n` +
`  ), membership_rows as (\n` +
`    select 'db-role-membership:'||role.rolname||':'||member.rolname ingress_key,jsonb_build_object('ingress_key','db-role-membership:'||role.rolname||':'||member.rolname,'row_kind','membership','role',role.rolname,'member',member.rolname,'admin_option',m.admin_option,'inherit_option',m.inherit_option,'set_option',m.set_option) row from pg_auth_members m join pg_roles role on role.oid=m.roleid join pg_roles member on member.oid=m.member where m.roleid in(select oid from connected) and m.member in(select oid from connected)\n` +
`  ), ownership_rows as (\n` +
`    select 'db-function-owner:'||n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||'):'||owner.rolname ingress_key,jsonb_build_object('ingress_key','db-function-owner:'||n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||'):'||owner.rolname,'row_kind','function_owner','signature',n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||')','owner',owner.rolname) row from pg_proc p join pg_namespace n on n.oid=p.pronamespace join pg_roles owner on owner.oid=p.proowner where n.nspname not in ('pg_catalog','information_schema') and p.prokind in ('f','p') and owner.oid in(select oid from connected) and not owner.rolsuper and owner.rolname<>'neondb_owner' union all\n` +
`    select 'db-relation-owner:'||n.nspname||'.'||c.relname||':'||owner.rolname,jsonb_build_object('ingress_key','db-relation-owner:'||n.nspname||'.'||c.relname||':'||owner.rolname,'row_kind','relation_owner','relation',n.nspname||'.'||c.relname,'relation_kind',c.relkind,'owner',owner.rolname) row from pg_class c join pg_namespace n on n.oid=c.relnamespace join pg_roles owner on owner.oid=c.relowner where n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f') and owner.oid in(select oid from connected) and not owner.rolsuper and owner.rolname<>'neondb_owner'\n` +
`  ), observed as (select * from role_rows union all select * from membership_rows union all select * from ownership_rows)\n` +
`  select count(*),'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(jsonb_agg(row order by ingress_key collate "C", ops.scac_canonical_json(row) collate "C"),'[]'::jsonb)),'UTF8'),'sha256'),'hex') into observed_count,observed_digest from observed;\n` +
`  return observed_count=${dbCatalogBaseline.role_authority.count} and observed_digest='${dbCatalogBaseline.role_authority.digest}';\n` +
`end $fn$;\n\n` +
`alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v2;\n` +
`create or replace function ops.scac_policy_epoch_snapshot()\n` +
`returns jsonb language plpgsql stable security definer set search_path=pg_catalog,public,ops as $fn$\n` +
`declare source jsonb; registry_ok jsonb;\n` +
`begin\n` +
`  source:=ops.scac_policy_epoch_snapshot_v2();\n` +
`  registry_ok:=ops.scac_mutation_registration_v3('sha256:${digest}','mcp-tool:standing-context');\n` +
`  if coalesce((registry_ok->>'registered')::boolean,false) is not true then\n` +
`    raise exception 'sealed SCAC mutation registry v3 is unavailable or corrupt: %',registry_ok->>'reason';\n` +
`  end if;\n` +
`  if not ops.scac_mutation_catalog_v3_current() then raise exception 'live SCAC v3 mutation catalog drifted'; end if;\n` +
`  return jsonb_set(jsonb_set(source,'{registry_version}',to_jsonb('${version}'::text)),\n` +
`    '{registry_digest}',to_jsonb('sha256:${digest}'::text));\n` +
`end $fn$;\n` +
`create or replace function ops.scac_policy_epoch_chain_state()\n` +
`returns jsonb language plpgsql stable security definer set search_path=pg_catalog,public,ops as $fn$\n` +
`declare r ops.scac_policy_epoch%rowtype; expected bigint:=1; prior_digest text:=null; recomputed_source text; recomputed_epoch text; source jsonb; latest ops.scac_policy_epoch%rowtype;\n` +
`begin\n` +
`  for r in select * from ops.scac_policy_epoch order by epoch loop\n` +
`    if r.epoch<>expected or (expected=1 and (r.previous_epoch is not null or r.previous_epoch_digest is not null))\n` +
`       or (expected>1 and (r.previous_epoch<>expected-1 or r.previous_epoch_digest is distinct from prior_digest)) then\n` +
`      return jsonb_build_object('valid',false,'reason','epoch_chain_gap_or_fork');\n` +
`    end if;\n` +
`    recomputed_source:='sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(jsonb_build_object(\n` +
`      'registry_version',r.registry_version,'registry_digest',r.registry_digest,\n` +
`      'doctrine_generation',r.doctrine_generation,'doctrine_projection_digest',r.doctrine_projection_digest,\n` +
`      'rule_projection_digest',r.rule_projection_digest,'schema_applied_count',r.schema_applied_count,\n` +
`      'schema_highest_migration',r.schema_highest_migration,'schema_ledger_digest',r.schema_ledger_digest)),'UTF8'),'sha256'),'hex');\n` +
`    recomputed_epoch:=ops.scac_policy_epoch_digest(r.epoch,coalesce(r.previous_epoch,0),coalesce(r.previous_epoch_digest,'bootstrap'),recomputed_source,r.created_at);\n` +
`    if not ((r.registry_version='${predecessor.version}' and r.registry_digest='${predecessor.digest}')\n` +
`         or (r.registry_version='${version}' and r.registry_digest='sha256:${digest}'))\n` +
`       or r.source_digest is distinct from recomputed_source or r.epoch_digest is distinct from recomputed_epoch\n` +
`       or r.created_at>clock_timestamp()+interval '1 minute' then\n` +
`      return jsonb_build_object('valid',false,'reason','epoch_digest_or_source_corrupt');\n` +
`    end if;\n` +
`    latest:=r; prior_digest:=r.epoch_digest; expected:=expected+1;\n` +
`  end loop;\n` +
`  if expected=1 then return jsonb_build_object('valid',false,'reason','epoch_ledger_unavailable'); end if;\n` +
`  begin source:=ops.scac_policy_epoch_snapshot(); exception when others then\n` +
`    return jsonb_build_object('valid',false,'reason','live_source_unavailable'); end;\n` +
`  return jsonb_build_object('valid',true,'reason','valid','current_epoch',latest.epoch,\n` +
`    'current_epoch_digest',latest.epoch_digest,'current_source_digest',latest.source_digest,\n` +
`    'live_source_digest','sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(source),'UTF8'),'sha256'),'hex'),\n` +
`    'registry_version',latest.registry_version,'registry_digest',latest.registry_digest,\n` +
`    'schema_highest_migration',latest.schema_highest_migration);\n` +
`end $fn$;\n` +
`alter table ops.scac_policy_epoch drop constraint scac_policy_epoch_registry_version_check;\n` +
`alter table ops.scac_policy_epoch drop constraint scac_policy_epoch_registry_digest_check;\n` +
`alter table ops.scac_policy_epoch add constraint scac_policy_epoch_registry_version_digest_check check (\n` +
`  (registry_version='${predecessor.version}' and registry_digest='${predecessor.digest}') or\n` +
`  (registry_version='${version}' and registry_digest='sha256:${digest}'));\n` +
`revoke all on function ops.scac_policy_epoch_snapshot(),ops.scac_policy_epoch_snapshot_v2(),ops.scac_mutation_catalog_v2_current(),ops.scac_mutation_catalog_v3_current() from public,carr_reader,carr_writer,carr_jobs,carr_authority;\n` +
`comment on function ops.scac_policy_epoch_snapshot() is 'SIEP-13 successor snapshot: current policy epochs bind mutation registry v3 while historical v2 epochs remain immutable.';\n\n` +
`create trigger scac_mutation_registry_version_sealed before insert or update or delete on ops.scac_mutation_registry_version for each row execute function ops.scac_mutation_registry_append_only();\n` +
`create trigger scac_mutation_registry_entry_sealed before insert or update or delete on ops.scac_mutation_registry_entry for each row execute function ops.scac_mutation_registry_append_only();\n`;
}

function renderSIEP14RegistrySql(rows = fullInventory(),
  dbCatalogBaseline = SIEP14_DB_CATALOG_BASELINE) {
  const v2Seal = registrySeal(REGISTRY_V2_VERSION, rows, SIEP12_DB_CATALOG_BASELINE);
  const v3Seal = registrySeal(REGISTRY_V3_VERSION, rows, SIEP13_DB_CATALOG_BASELINE);
  const fakeV3Digest = registryDigestFor(REGISTRY_V3_VERSION, rows, dbCatalogBaseline);
  const v4Digest = registryDigestFor(REGISTRY_V4_VERSION, rows, dbCatalogBaseline);
  let sql = renderSIEP13RegistrySql(rows, dbCatalogBaseline)
    .replaceAll(fakeV3Digest, v4Digest)
    .replaceAll("SIEP-13", "SIEP-14")
    .replaceAll("SCAC-03", "SCAC-04")
    .replaceAll("scac-mutation-registry.v3", "scac-mutation-registry.v4")
    .replaceAll("_v3", "_v4")
    .replaceAll(" v3", " v4");
  sql = sql
    .replace("check (registry_version in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v4'))",
      "check (registry_version in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v4'))")
    .replace(`if (select registry_digest from ops.scac_mutation_registry_version where registry_version='${v2Seal.version}')<>'${v2Seal.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='${v2Seal.version}')<>${v2Seal.entryCount} then raise exception 'sealed SCAC mutation registry v2 changed during successor creation'; end if;`,
      `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='${v3Seal.version}')<>'${v3Seal.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='${v3Seal.version}')<>${v3Seal.entryCount} then raise exception 'sealed SCAC mutation registry v3 changed during successor creation'; end if;`)
    .replaceAll("scac_mutation_catalog_v2_current", "scac_mutation_catalog_v3_current")
    .replaceAll(`registry_version='${v2Seal.version}'\n      and registry_digest='${v2Seal.digest}'\n      and entry_count=${v2Seal.entryCount} and source_entry_count=${v2Seal.sourceEntryCount}`,
      `registry_version='${v3Seal.version}'\n      and registry_digest='${v3Seal.digest}'\n      and entry_count=${v3Seal.entryCount} and source_entry_count=${v3Seal.sourceEntryCount}`)
    .replaceAll("Historical v2 seal availability after v4; it is not a claim that the live catalog remains v3.",
      "Historical v3 seal availability after v4; it is not a claim that the live catalog remains v3.")
    .replace("alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v2;",
      "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v3;")
    .replaceAll("scac_policy_epoch_snapshot_v2()", "scac_policy_epoch_snapshot_v3()")
    .replace("alter table ops.scac_policy_epoch drop constraint scac_policy_epoch_registry_version_check;\nalter table ops.scac_policy_epoch drop constraint scac_policy_epoch_registry_digest_check;",
      "alter table ops.scac_policy_epoch drop constraint scac_policy_epoch_registry_version_digest_check;")
    .replace(`if not ((r.registry_version='${v2Seal.version}' and r.registry_digest='${v2Seal.digest}')\n         or (r.registry_version='scac-mutation-registry.v4'`,
      `if not ((r.registry_version='${v2Seal.version}' and r.registry_digest='${v2Seal.digest}')\n         or (r.registry_version='${v3Seal.version}' and r.registry_digest='${v3Seal.digest}')\n         or (r.registry_version='scac-mutation-registry.v4'`)
    .replace(`(registry_version='${v2Seal.version}' and registry_digest='${v2Seal.digest}') or\n  (registry_version='scac-mutation-registry.v4'`,
      `(registry_version='${v2Seal.version}' and registry_digest='${v2Seal.digest}') or\n  (registry_version='${v3Seal.version}' and registry_digest='${v3Seal.digest}') or\n  (registry_version='scac-mutation-registry.v4'`)
    .replaceAll("SIEP-14 successor snapshot: current policy epochs bind mutation registry v4 while historical v2 epochs remain immutable.",
      "SIEP-14 successor snapshot: current policy epochs bind mutation registry v4 while historical v2/v3 epochs remain immutable.");
  return sql;
}

export function renderSIEP15RegistrySql(rows = fullInventory(),
  dbCatalogBaseline = SIEP15_DB_CATALOG_BASELINE) {
  const { v1: v1Seal, v2: v2Seal, v3: v3Seal, v4: v4Seal } = HISTORICAL_REGISTRY_SEALS;
  const v5Digest = registryDigestFor(REGISTRY_V5_VERSION, rows, dbCatalogBaseline);
  const v5CatalogCount = dbCatalogBaseline.secdef_execute.count +
    dbCatalogBaseline.relation_dml.count + dbCatalogBaseline.column_dml.count;
  const v5EntryCount = rows.length + v5CatalogCount;
  const v4Path = "migrations/0459_siep14_forward_mutation_registry.sql";
  const v4Sql = readFileSync(resolve(REPO_ROOT, v4Path), "utf8");
  const observedV4Sha = sha256(v4Sql);
  if (observedV4Sha !== HISTORICAL_REGISTRY_ARTIFACT_SHA256[v4Path])
    throw new Error(`sealed historical SCAC v4 migration changed: ${observedV4Sha}`);
  let sql = v4Sql
    .replaceAll(JSON.stringify(SIEP14_DB_CATALOG_BASELINE), JSON.stringify(dbCatalogBaseline))
    .replaceAll(
      `if observed_count<>${SIEP14_DB_CATALOG_BASELINE.secdef_execute.count} or observed_digest<>'${SIEP14_DB_CATALOG_BASELINE.secdef_execute.digest}' then return false; end if;`,
      `if observed_count<>${dbCatalogBaseline.secdef_execute.count} or observed_digest<>'${dbCatalogBaseline.secdef_execute.digest}' then return false; end if;`,
    )
    .replaceAll(v4Seal.digest.slice("sha256:".length), v5Digest)
    .replaceAll("SIEP-14", "SIEP-15")
    .replaceAll("SCAC-04", "SCAC-05")
    .replaceAll("scac-mutation-registry.v4", "scac-mutation-registry.v5")
    .replaceAll("_v4", "_v5")
    .replaceAll(" v4", " v5");
  sql = sql
    .replace(`'sha256:${v5Digest}',${v4Seal.entryCount},${v4Seal.sourceEntryCount},`,
      `'sha256:${v5Digest}',${v5EntryCount},${rows.length},`)
    .replace(`where registry_version='${REGISTRY_V5_VERSION}')<>${v4Seal.entryCount}\n`,
      `where registry_version='${REGISTRY_V5_VERSION}')<>${v5EntryCount}\n`);
  const seedStartMarker = "with seed as (select value as contract from jsonb_array_elements(";
  const seedEndMarker = "::jsonb))\ninsert into ops.scac_mutation_registry_entry";
  const seedStart = sql.indexOf(seedStartMarker) + seedStartMarker.length;
  const seedEnd = sql.indexOf(seedEndMarker, seedStart);
  if (seedStart < seedStartMarker.length || seedEnd < seedStart)
    throw new Error("sealed SCAC v4 migration has no exact source-seed boundary");
  const seed = JSON.stringify(rows.map(row => ({ ...row, entry_digest: `sha256:${sha256(row)}` })));
  sql = `${sql.slice(0, seedStart)}${sqlLiteral(seed)}${sql.slice(seedEnd)}`;
  sql = sql
    .replace("check (registry_version in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v5'))",
      "check (registry_version in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v4','scac-mutation-registry.v5'))")
    .replace(`if (select registry_digest from ops.scac_mutation_registry_version where registry_version='${v3Seal.version}')<>'${v3Seal.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='${v3Seal.version}')<>${v3Seal.entryCount} then raise exception 'sealed SCAC mutation registry v3 changed during successor creation'; end if;`,
      `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='${v4Seal.version}')<>'${v4Seal.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='${v4Seal.version}')<>${v4Seal.entryCount} then raise exception 'sealed SCAC mutation registry v4 changed during successor creation'; end if;`)
    .replace("alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v3;",
      "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v4;")
    .replaceAll("scac_policy_epoch_snapshot_v3()", "scac_policy_epoch_snapshot_v4()")
    .replace(`if not ((r.registry_version='${v2Seal.version}' and r.registry_digest='${v2Seal.digest}')\n         or (r.registry_version='${v3Seal.version}' and r.registry_digest='${v3Seal.digest}')\n         or (r.registry_version='scac-mutation-registry.v5'`,
      `if not ((r.registry_version='${v2Seal.version}' and r.registry_digest='${v2Seal.digest}')\n         or (r.registry_version='${v3Seal.version}' and r.registry_digest='${v3Seal.digest}')\n         or (r.registry_version='${v4Seal.version}' and r.registry_digest='${v4Seal.digest}')\n         or (r.registry_version='scac-mutation-registry.v5'`)
    .replace(`(registry_version='${v2Seal.version}' and registry_digest='${v2Seal.digest}') or\n  (registry_version='${v3Seal.version}' and registry_digest='${v3Seal.digest}') or\n  (registry_version='scac-mutation-registry.v5'`,
      `(registry_version='${v2Seal.version}' and registry_digest='${v2Seal.digest}') or\n  (registry_version='${v3Seal.version}' and registry_digest='${v3Seal.digest}') or\n  (registry_version='${v4Seal.version}' and registry_digest='${v4Seal.digest}') or\n  (registry_version='scac-mutation-registry.v5'`)
    .replaceAll("SIEP-15 successor snapshot: current policy epochs bind mutation registry v5 while historical v2/v3 epochs remain immutable.",
      "SIEP-15 successor snapshot: current policy epochs bind mutation registry v5 while historical v2/v3/v4 epochs remain immutable.");
  const v4HistoryVerifier = `-- Preserve the v4 live-catalog validator under an honest historical name.\n` +
`alter function ops.scac_mutation_catalog_v4_current() rename to scac_mutation_catalog_v4_live_at_seal;\n` +
`create or replace function ops.scac_mutation_registry_seal_valid(p_registry_version text)\n` +
`returns boolean language plpgsql stable security definer set search_path=pg_catalog,public,ops as $fn$\n` +
`declare v ops.scac_mutation_registry_version%rowtype; actual_count integer; actual_source_count integer;\n` +
`        actual_set text; bad_hash boolean; category text; kind text; expected_registry text;\n` +
`        actual_category_count integer; actual_category_digest text; expected jsonb; expected_catalog jsonb;\n` +
`begin\n` +
`  if p_registry_version not in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v4') then return false; end if;\n` +
`  expected_registry:=case p_registry_version\n` +
`    when '${v1Seal.version}' then '${v1Seal.digest}'\n` +
`    when '${v2Seal.version}' then '${v2Seal.digest}'\n` +
`    when '${v3Seal.version}' then '${v3Seal.digest}'\n` +
`    when '${v4Seal.version}' then '${v4Seal.digest}' end;\n` +
`  expected_catalog:=case p_registry_version\n` +
`    when 'scac-mutation-registry.v1' then ${sqlLiteral(JSON.stringify(DB_CATALOG_BASELINE))}::jsonb\n` +
`    when 'scac-mutation-registry.v2' then ${sqlLiteral(JSON.stringify(SIEP12_DB_CATALOG_BASELINE))}::jsonb\n` +
`    when 'scac-mutation-registry.v3' then ${sqlLiteral(JSON.stringify(SIEP13_DB_CATALOG_BASELINE))}::jsonb\n` +
`    when 'scac-mutation-registry.v4' then ${sqlLiteral(JSON.stringify(SIEP14_DB_CATALOG_BASELINE))}::jsonb end;\n` +
`  select * into v from ops.scac_mutation_registry_version where registry_version=p_registry_version;\n` +
`  if v.registry_version is null or v.registry_digest is distinct from expected_registry or\n` +
`     v.catalog_projection is distinct from expected_catalog then return false; end if;\n` +
`  select count(*),count(*) filter(where ingress_kind not in ('db_function_acl','db_relation_acl','db_column_acl')),\n` +
`    'sha256:'||encode(public.digest(convert_to(coalesce(string_agg(entry_digest,',' order by ingress_key collate "C", entry_digest collate "C"),''),'UTF8'),'sha256'),'hex'),\n` +
`    coalesce(bool_or(entry_digest is distinct from 'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(contract),'UTF8'),'sha256'),'hex')),false)\n` +
`    into actual_count,actual_source_count,actual_set,bad_hash\n` +
`    from ops.scac_mutation_registry_entry where registry_version=p_registry_version;\n` +
`  if actual_count<>v.entry_count or actual_source_count<>v.source_entry_count or\n` +
`     actual_set is distinct from v.entry_set_digest or bad_hash then return false; end if;\n` +
`  for category,kind in values ('secdef_execute','db_function_acl'),('relation_dml','db_relation_acl'),('column_dml','db_column_acl') loop\n` +
`    select count(*),'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(jsonb_agg(contract-'effect_class'-'owner_package'-'implementation_state'-'classification_authorizing'-'source_locator' order by ingress_key collate "C", ops.scac_canonical_json(contract-'effect_class'-'owner_package'-'implementation_state'-'classification_authorizing'-'source_locator') collate "C"),'[]'::jsonb)),'UTF8'),'sha256'),'hex')\n` +
`      into actual_category_count,actual_category_digest from ops.scac_mutation_registry_entry\n` +
`      where registry_version=p_registry_version and ingress_kind=kind;\n` +
`    expected:=v.catalog_projection->category;\n` +
`    if actual_category_count<>(expected->>'count')::integer or actual_category_digest<>expected->>'digest' then return false; end if;\n` +
`  end loop;\n` +
`  return true;\n` +
`end $fn$;\n` +
`create or replace function ops.scac_mutation_registry_v4_seal_available()\n` +
`returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$\n` +
`  select ops.scac_mutation_registry_seal_valid('scac-mutation-registry.v4')\n` +
`$fn$;\n` +
`create or replace function ops.scac_mutation_catalog_v4_current()\n` +
`returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$\n` +
`  select ops.scac_mutation_catalog_v4_live_at_seal()\n` +
`$fn$;\n` +
`comment on function ops.scac_mutation_registry_v4_seal_available() is 'Exact immutable v4 registry seal; separate from whether the live catalog still equals v4.';\n` +
`comment on function ops.scac_mutation_catalog_v4_current() is 'Historical v4 live-catalog validator; expected to become false after the v5 authority surface is installed.';\n` +
`revoke all on function ops.scac_mutation_registry_seal_valid(text),ops.scac_mutation_registry_v4_seal_available(),\n` +
`  ops.scac_mutation_catalog_v4_live_at_seal(),ops.scac_mutation_catalog_v4_current()\n` +
`  from public,carr_reader,carr_writer,carr_jobs,carr_authority;\n` +
`do $history$ declare expected record; actual ops.scac_mutation_registry_version%rowtype;\n` +
`begin\n` +
`  for expected in select * from (values\n` +
`    ('${v1Seal.version}','${v1Seal.digest}',${v1Seal.entryCount},${v1Seal.sourceEntryCount}),\n` +
`    ('${v2Seal.version}','${v2Seal.digest}',${v2Seal.entryCount},${v2Seal.sourceEntryCount}),\n` +
`    ('${v3Seal.version}','${v3Seal.digest}',${v3Seal.entryCount},${v3Seal.sourceEntryCount}),\n` +
`    ('${v4Seal.version}','${v4Seal.digest}',${v4Seal.entryCount},${v4Seal.sourceEntryCount})\n` +
`  ) as x(registry_version,registry_digest,entry_count,source_entry_count) loop\n` +
`    select * into actual from ops.scac_mutation_registry_version where registry_version=expected.registry_version;\n` +
`    if actual.registry_digest is distinct from expected.registry_digest or actual.entry_count<>expected.entry_count or\n` +
`       actual.source_entry_count<>expected.source_entry_count or not ops.scac_mutation_registry_seal_valid(expected.registry_version) then\n` +
`      raise exception 'sealed historical SCAC registry % is missing or corrupt',expected.registry_version;\n` +
`    end if;\n` +
`  end loop;\n` +
`end $history$;\n\n`;
  sql = sql
    .replace("create or replace function ops.scac_mutation_catalog_v5_current()", `${v4HistoryVerifier}create or replace function ops.scac_mutation_catalog_v5_current()`)
    .replace("source:=ops.scac_policy_epoch_snapshot_v4();",
      "source:=ops.scac_policy_epoch_snapshot_v3();\n  if not (ops.scac_mutation_registry_seal_valid('scac-mutation-registry.v1') and\n    ops.scac_mutation_registry_seal_valid('scac-mutation-registry.v2') and\n    ops.scac_mutation_registry_seal_valid('scac-mutation-registry.v3') and\n    ops.scac_mutation_registry_v4_seal_available()) then\n    raise exception 'sealed historical SCAC mutation registry is unavailable or corrupt';\n  end if;")
    .replace("ops.scac_mutation_catalog_v4_current(),ops.scac_mutation_catalog_v5_current()",
      "ops.scac_mutation_catalog_v3_current(),ops.scac_mutation_catalog_v4_live_at_seal(),ops.scac_mutation_catalog_v4_current(),ops.scac_mutation_registry_seal_valid(text),ops.scac_mutation_registry_v4_seal_available(),ops.scac_mutation_catalog_v5_current()")
    .replace("-- Retain v2 as an exact historical lookup after the live catalog advances.",
      "-- Retain the already-sealed v3 metadata lookup; v4 live and seal predicates remain separate below.")
    .replace("Historical v2 seal availability after v5; it is not a claim that the live catalog remains v2.",
      "Historical v3 seal availability after v5; it is not a claim that the live catalog remains v3.");
  return sql;
}

export function renderSIEP16RegistrySql(rows = fullInventory(),
  dbCatalogBaseline = SIEP16_DB_CATALOG_BASELINE) {
  const { v4: v4Seal, v5: v5Seal } = HISTORICAL_REGISTRY_SEALS;
  const v6Digest = registryDigestFor(REGISTRY_V6_VERSION, rows, dbCatalogBaseline);
  const v6CatalogCount = dbCatalogBaseline.secdef_execute.count +
    dbCatalogBaseline.relation_dml.count + dbCatalogBaseline.column_dml.count;
  const v6EntryCount = rows.length + v6CatalogCount;
  const v5Path = "migrations/0461_siep15_forward_mutation_registry.sql";
  const v5Sql = readFileSync(resolve(REPO_ROOT, v5Path), "utf8");
  const observedV5Sha = sha256(v5Sql);
  if (observedV5Sha !== HISTORICAL_REGISTRY_ARTIFACT_SHA256[v5Path])
    throw new Error(`sealed historical SCAC v5 migration changed: ${observedV5Sha}`);
  let sql = v5Sql
    .replaceAll(JSON.stringify(SIEP15_DB_CATALOG_BASELINE), JSON.stringify(dbCatalogBaseline))
    .replaceAll(
      `if observed_count<>${SIEP15_DB_CATALOG_BASELINE.secdef_execute.count} or observed_digest<>'${SIEP15_DB_CATALOG_BASELINE.secdef_execute.digest}' then return false; end if;`,
      `if observed_count<>${dbCatalogBaseline.secdef_execute.count} or observed_digest<>'${dbCatalogBaseline.secdef_execute.digest}' then return false; end if;`,
    )
    .replaceAll(v5Seal.digest.slice("sha256:".length), v6Digest)
    .replaceAll("SIEP-15", "SIEP-16")
    .replaceAll("SCAC-05", "SCAC-06")
    .replaceAll("scac-mutation-registry.v5", "scac-mutation-registry.v6")
    .replaceAll("_v5", "_v6")
    .replaceAll(" v5", " v6");
  sql = sql
    .replace(`'sha256:${v6Digest}',${v5Seal.entryCount},${v5Seal.sourceEntryCount},`,
      `'sha256:${v6Digest}',${v6EntryCount},${rows.length},`)
    .replace(`where registry_version='${REGISTRY_V6_VERSION}')<>${v5Seal.entryCount}\n`,
      `where registry_version='${REGISTRY_V6_VERSION}')<>${v6EntryCount}\n`);
  const seedStartMarker = "with seed as (select value as contract from jsonb_array_elements(";
  const seedEndMarker = "::jsonb))\ninsert into ops.scac_mutation_registry_entry";
  const seedStart = sql.indexOf(seedStartMarker) + seedStartMarker.length;
  const seedEnd = sql.indexOf(seedEndMarker, seedStart);
  if (seedStart < seedStartMarker.length || seedEnd < seedStart)
    throw new Error("sealed SCAC v5 migration has no exact source-seed boundary");
  const seed = JSON.stringify(rows.map(row => ({ ...row, entry_digest: `sha256:${sha256(row)}` })));
  sql = `${sql.slice(0, seedStart)}${sqlLiteral(seed)}${sql.slice(seedEnd)}`;
  const v5Catalog = JSON.stringify(SIEP15_DB_CATALOG_BASELINE);
  sql = sql
    .replace("-- Preserve the v4 live-catalog validator under an honest historical name.\nalter function ops.scac_mutation_catalog_v4_current() rename to scac_mutation_catalog_v4_live_at_seal;\n", "")
    .replace("check (registry_version in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v4','scac-mutation-registry.v6'))",
      "check (registry_version in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v4','scac-mutation-registry.v5','scac-mutation-registry.v6'))")
    .replace("if p_registry_version not in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v4') then return false; end if;",
      "if p_registry_version not in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v4','scac-mutation-registry.v5') then return false; end if;");
  sql = replaceExactlyOnce(sql,
    `    when '${v4Seal.version}' then '${v4Seal.digest}' end;`,
    `    when '${v4Seal.version}' then '${v4Seal.digest}'\n    when '${v5Seal.version}' then '${v5Seal.digest}' end;`,
    "SIEP-16 predecessor digest case");
  sql = replaceExactlyOnce(sql,
    `    when '${v4Seal.version}' then '${JSON.stringify(SIEP14_DB_CATALOG_BASELINE)}'::jsonb end;`,
    `    when '${v4Seal.version}' then '${JSON.stringify(SIEP14_DB_CATALOG_BASELINE)}'::jsonb\n    when '${v5Seal.version}' then '${v5Catalog}'::jsonb end;`,
    "SIEP-16 predecessor catalog case");
  sql = replaceExactlyOnce(sql,
    `    ('${v4Seal.version}','${v4Seal.digest}',${v4Seal.entryCount},${v4Seal.sourceEntryCount})\n`,
    `    ('${v4Seal.version}','${v4Seal.digest}',${v4Seal.entryCount},${v4Seal.sourceEntryCount}),\n    ('${v5Seal.version}','${v5Seal.digest}',${v5Seal.entryCount},${v5Seal.sourceEntryCount})\n`,
    "SIEP-16 predecessor history tuple");
  sql = replaceExactlyOnce(sql,
    `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='${v4Seal.version}')<>'${v4Seal.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='${v4Seal.version}')<>${v4Seal.entryCount} then raise exception 'sealed SCAC mutation registry v4 changed during successor creation'; end if;`,
    `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='${v5Seal.version}')<>'${v5Seal.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='${v5Seal.version}')<>${v5Seal.entryCount} then raise exception 'sealed SCAC mutation registry v5 changed during successor creation'; end if;`,
    "SIEP-16 predecessor seal guard");
  sql = sql
    .replace("create or replace function ops.scac_mutation_catalog_v6_current()",
`alter function ops.scac_mutation_catalog_v5_current() rename to scac_mutation_catalog_v5_live_at_seal;
create or replace function ops.scac_mutation_registry_v5_seal_available()
returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$
  select ops.scac_mutation_registry_seal_valid('scac-mutation-registry.v5')
$fn$;
create or replace function ops.scac_mutation_catalog_v5_current()
returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$
  select ops.scac_mutation_catalog_v5_live_at_seal()
$fn$;
comment on function ops.scac_mutation_registry_v5_seal_available() is 'Exact immutable v5 registry seal; separate from whether the live catalog still equals v5.';
comment on function ops.scac_mutation_catalog_v5_current() is 'Historical v5 live-catalog validator; expected to be false after the v6 authority surface is installed.';

create or replace function ops.scac_mutation_catalog_v6_current()`)
    .replace("alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v4;",
      "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v5;")
    .replace("    ops.scac_mutation_registry_v4_seal_available()) then",
      "    ops.scac_mutation_registry_v4_seal_available() and\n    ops.scac_mutation_registry_v5_seal_available()) then")
    .replace("         or (r.registry_version='scac-mutation-registry.v6'",
      `         or (r.registry_version='${v5Seal.version}' and r.registry_digest='${v5Seal.digest}')\n         or (r.registry_version='scac-mutation-registry.v6'`)
    .replace("  (registry_version='scac-mutation-registry.v6'",
      `  (registry_version='${v5Seal.version}' and registry_digest='${v5Seal.digest}') or\n  (registry_version='scac-mutation-registry.v6'`)
    .replace("ops.scac_policy_epoch_snapshot_v4(),ops.scac_mutation_catalog_v3_current(),ops.scac_mutation_catalog_v6_current()",
      "ops.scac_policy_epoch_snapshot_v5(),ops.scac_mutation_catalog_v3_current(),ops.scac_mutation_catalog_v5_live_at_seal(),ops.scac_mutation_catalog_v5_current(),ops.scac_mutation_registry_v5_seal_available(),ops.scac_mutation_catalog_v6_current()")
    .replaceAll("SIEP-16 successor snapshot: current policy epochs bind mutation registry v6 while historical v2/v3/v4 epochs remain immutable.",
      "SIEP-16 successor snapshot: current policy epochs bind mutation registry v6 while historical v2/v3/v4/v5 epochs remain immutable.");
  return sql;
}

export function renderSIEP16IntegratedRegistrySql(rows = fullInventory(),
  dbCatalogBaseline = SIEP16_INTEGRATED_DB_CATALOG_BASELINE) {
  const { v5: v5Seal, v6: v6Seal } = HISTORICAL_REGISTRY_SEALS;
  const v7Digest = registryDigestFor(REGISTRY_V7_VERSION, rows, dbCatalogBaseline);
  const v7CatalogCount = dbCatalogBaseline.secdef_execute.count +
    dbCatalogBaseline.relation_dml.count + dbCatalogBaseline.column_dml.count;
  const v7EntryCount = rows.length + v7CatalogCount;
  const v6Path = "migrations/0462_siep16_forward_mutation_registry.sql";
  const v6Sql = readFileSync(resolve(REPO_ROOT, v6Path), "utf8");
  const observedV6Sha = sha256(v6Sql);
  if (observedV6Sha !== HISTORICAL_REGISTRY_ARTIFACT_SHA256[v6Path])
    throw new Error(`sealed historical SCAC v6 migration changed: ${observedV6Sha}`);
  let sql = v6Sql
    .replaceAll(JSON.stringify(SIEP16_DB_CATALOG_BASELINE), JSON.stringify(dbCatalogBaseline))
    .replaceAll(
      `if observed_count<>${SIEP16_DB_CATALOG_BASELINE.secdef_execute.count} or observed_digest<>'${SIEP16_DB_CATALOG_BASELINE.secdef_execute.digest}' then return false; end if;`,
      `if observed_count<>${dbCatalogBaseline.secdef_execute.count} or observed_digest<>'${dbCatalogBaseline.secdef_execute.digest}' then return false; end if;`,
    )
    .replaceAll(v6Seal.digest.slice("sha256:".length), v7Digest)
    .replaceAll("scac-mutation-registry.v6", "scac-mutation-registry.v7")
    .replaceAll("_v6", "_v7")
    .replaceAll(" v6", " v7");
  sql = sql
    .replace(`'sha256:${v7Digest}',${v6Seal.entryCount},${v6Seal.sourceEntryCount},`,
      `'sha256:${v7Digest}',${v7EntryCount},${rows.length},`)
    .replace(`where registry_version='${REGISTRY_V7_VERSION}')<>${v6Seal.entryCount}\n`,
      `where registry_version='${REGISTRY_V7_VERSION}')<>${v7EntryCount}\n`);
  const seedStartMarker = "with seed as (select value as contract from jsonb_array_elements(";
  const seedEndMarker = "::jsonb))\ninsert into ops.scac_mutation_registry_entry";
  const seedStart = sql.indexOf(seedStartMarker) + seedStartMarker.length;
  const seedEnd = sql.indexOf(seedEndMarker, seedStart);
  if (seedStart < seedStartMarker.length || seedEnd < seedStart)
    throw new Error("sealed SCAC v6 migration has no exact source-seed boundary");
  const seed = JSON.stringify(rows.map(row => ({ ...row, entry_digest: `sha256:${sha256(row)}` })));
  sql = `${sql.slice(0, seedStart)}${sqlLiteral(seed)}${sql.slice(seedEnd)}`;
  const v6Catalog = JSON.stringify(SIEP16_DB_CATALOG_BASELINE);
  sql = sql
    .replace("check (registry_version in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v4','scac-mutation-registry.v5','scac-mutation-registry.v7'))",
      "check (registry_version in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v4','scac-mutation-registry.v5','scac-mutation-registry.v6','scac-mutation-registry.v7'))")
    .replace("if p_registry_version not in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v4','scac-mutation-registry.v5') then return false; end if;",
      "if p_registry_version not in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v4','scac-mutation-registry.v5','scac-mutation-registry.v6') then return false; end if;");
  sql = replaceExactlyOnce(sql,
    `    when '${v5Seal.version}' then '${v5Seal.digest}' end;`,
    `    when '${v5Seal.version}' then '${v5Seal.digest}'\n    when '${v6Seal.version}' then '${v6Seal.digest}' end;`,
    "SIEP-16 integrated predecessor digest case");
  sql = replaceExactlyOnce(sql,
    `    when '${v5Seal.version}' then '${JSON.stringify(SIEP15_DB_CATALOG_BASELINE)}'::jsonb end;`,
    `    when '${v5Seal.version}' then '${JSON.stringify(SIEP15_DB_CATALOG_BASELINE)}'::jsonb\n    when '${v6Seal.version}' then '${v6Catalog}'::jsonb end;`,
    "SIEP-16 integrated predecessor catalog case");
  sql = replaceExactlyOnce(sql,
    `    ('${v5Seal.version}','${v5Seal.digest}',${v5Seal.entryCount},${v5Seal.sourceEntryCount})\n`,
    `    ('${v5Seal.version}','${v5Seal.digest}',${v5Seal.entryCount},${v5Seal.sourceEntryCount}),\n    ('${v6Seal.version}','${v6Seal.digest}',${v6Seal.entryCount},${v6Seal.sourceEntryCount})\n`,
    "SIEP-16 integrated predecessor history tuple");
  sql = replaceExactlyOnce(sql,
    `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='${v5Seal.version}')<>'${v5Seal.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='${v5Seal.version}')<>${v5Seal.entryCount} then raise exception 'sealed SCAC mutation registry v5 changed during successor creation'; end if;`,
    `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='${v6Seal.version}')<>'${v6Seal.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='${v6Seal.version}')<>${v6Seal.entryCount} then raise exception 'sealed SCAC mutation registry v6 changed during successor creation'; end if;`,
    "SIEP-16 integrated predecessor seal guard");
  sql = sql
    .replace("alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v5;",
      "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v6;")
    .replace("    ops.scac_mutation_registry_v5_seal_available()) then",
      "    ops.scac_mutation_registry_v5_seal_available() and\n    ops.scac_mutation_registry_v6_seal_available()) then")
    .replace("         or (r.registry_version='scac-mutation-registry.v7'",
      `         or (r.registry_version='${v6Seal.version}' and r.registry_digest='${v6Seal.digest}')\n         or (r.registry_version='scac-mutation-registry.v7'`)
    .replace("  (registry_version='scac-mutation-registry.v7'",
      `  (registry_version='${v6Seal.version}' and registry_digest='${v6Seal.digest}') or\n  (registry_version='scac-mutation-registry.v7'`)
    .replace("ops.scac_policy_epoch_snapshot_v5(),ops.scac_mutation_catalog_v3_current(),ops.scac_mutation_catalog_v5_live_at_seal(),ops.scac_mutation_catalog_v5_current(),ops.scac_mutation_registry_v5_seal_available(),ops.scac_mutation_catalog_v7_current()",
      "ops.scac_policy_epoch_snapshot_v6(),ops.scac_mutation_catalog_v3_current(),ops.scac_mutation_catalog_v5_live_at_seal(),ops.scac_mutation_catalog_v5_current(),ops.scac_mutation_registry_v5_seal_available(),ops.scac_mutation_catalog_v6_live_at_seal(),ops.scac_mutation_catalog_v6_current(),ops.scac_mutation_registry_v6_seal_available(),ops.scac_mutation_catalog_v7_current()")
    .replaceAll("SIEP-16 successor snapshot: current policy epochs bind mutation registry v7 while historical v2/v3/v4/v5 epochs remain immutable.",
      "SIEP-16 integrated successor snapshot: current policy epochs bind mutation registry v7 while historical v2/v3/v4/v5/v6 epochs remain immutable.");
  const historyStart = sql.indexOf("alter function ops.scac_mutation_catalog_v5_current() rename to scac_mutation_catalog_v5_live_at_seal;");
  const currentV7 = "create or replace function ops.scac_mutation_catalog_v7_current()";
  const historyEnd = sql.indexOf(currentV7, historyStart);
  if (historyStart < 0 || historyEnd < historyStart)
    throw new Error("sealed SCAC v6 migration has no exact live-catalog successor boundary");
  const v6History = `alter function ops.scac_mutation_catalog_v6_current() rename to scac_mutation_catalog_v6_live_at_seal;\n` +
`create or replace function ops.scac_mutation_registry_v6_seal_available()\n` +
`returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$\n` +
`  select ops.scac_mutation_registry_seal_valid('scac-mutation-registry.v6')\n` +
`$fn$;\n` +
`create or replace function ops.scac_mutation_catalog_v6_current()\n` +
`returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$\n` +
`  select ops.scac_mutation_catalog_v6_live_at_seal()\n` +
`$fn$;\n` +
`comment on function ops.scac_mutation_registry_v6_seal_available() is 'Exact immutable v6 registry seal; separate from whether the live catalog still equals v6.';\n` +
`comment on function ops.scac_mutation_catalog_v6_current() is 'Historical v6 live-catalog validator; expected to be false after the v7 authority surface is installed.';\n\n`;
  return `${sql.slice(0, historyStart)}${v6History}${sql.slice(historyEnd)}`;
}

export function renderSIEP17ForwardRegistrySql(rows = fullInventory(),
  dbCatalogBaseline = SIEP17_FORWARD_DB_CATALOG_BASELINE) {
  const { v7: v7Seal } = HISTORICAL_REGISTRY_SEALS;
  const v8Digest = registryDigestFor(REGISTRY_V8_VERSION, rows, dbCatalogBaseline);
  const catalogCount = dbCatalogBaseline.secdef_execute.count +
    dbCatalogBaseline.relation_dml.count + dbCatalogBaseline.column_dml.count;
  const entryCount = rows.length + catalogCount;
  const v7MigrationPath = "migrations/0464_siep16_integrated_mutation_registry.sql";
  const v7RuntimePath = "mcp-server/src/scac-mutation-registry.v7.generated.js";
  const v7Migration = readFileSync(resolve(REPO_ROOT, v7MigrationPath), "utf8");
  for (const path of [v7MigrationPath, v7RuntimePath]) {
    const observed = sha256(readFileSync(resolve(REPO_ROOT, path), "utf8"));
    if (observed !== HISTORICAL_REGISTRY_ARTIFACT_SHA256[path])
      throw new Error(`sealed historical SCAC v7 artifact changed: ${path}: ${observed}`);
  }
  const currentV7Start = v7Migration.indexOf("create or replace function ops.scac_mutation_catalog_v7_current()");
  const policyStart = v7Migration.indexOf("alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v6;");
  if (currentV7Start < 0 || policyStart < currentV7Start)
    throw new Error("sealed SCAC v7 migration has no exact catalog successor boundary");
  const v7Current = v7Migration.slice(currentV7Start, policyStart);
  const v7History = `alter function ops.scac_mutation_catalog_v7_current() rename to scac_mutation_catalog_v7_live_at_seal;\n` +
`create or replace function ops.scac_mutation_registry_v7_seal_available()\n` +
`returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$\n` +
`  select ops.scac_mutation_registry_seal_valid('scac-mutation-registry.v7')\n` +
`$fn$;\n` +
`create or replace function ops.scac_mutation_catalog_v7_current()\n` +
`returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$\n` +
`  select ops.scac_mutation_catalog_v7_live_at_seal()\n` +
`$fn$;\n` +
`comment on function ops.scac_mutation_registry_v7_seal_available() is 'Exact immutable v7 registry seal; separate from whether the live catalog still equals v7.';\n` +
`comment on function ops.scac_mutation_catalog_v7_current() is 'Historical v7 live-catalog validator; expected to become false after the v8 authority surface is installed.';\n\n`;
  const v8Current = v7Current
    .replaceAll("scac_mutation_catalog_v7_current", "scac_mutation_catalog_v8_current")
    .replaceAll("scac-mutation-registry.v7", "scac-mutation-registry.v8")
    .replace(`if observed_count<>${SIEP16_INTEGRATED_DB_CATALOG_BASELINE.secdef_execute.count} or observed_digest<>'${SIEP16_INTEGRATED_DB_CATALOG_BASELINE.secdef_execute.digest}' then return false; end if;`,
      `if observed_count<>${dbCatalogBaseline.secdef_execute.count} or observed_digest<>'${dbCatalogBaseline.secdef_execute.digest}' then return false; end if;`);
  let sql = v7Migration.replace(v7Current, "__SIEP17_V7_CATALOG_SUCCESSOR__")
    .replace("-- SIEP-16 / SCAC-06: forward-only mutation registry v7.",
      "-- SIEP-17 / SCAC-07: forward-only mutation registry v8.")
    .replaceAll("scac-mutation-registry.v7", "scac-mutation-registry.v8")
    .replaceAll("_v7", "_v8")
    .replaceAll(" v7", " v8")
    .replaceAll(JSON.stringify(SIEP16_INTEGRATED_DB_CATALOG_BASELINE), JSON.stringify(dbCatalogBaseline))
    .replace(`'sha256:${v7Seal.digest.slice("sha256:".length)}',${v7Seal.entryCount},${v7Seal.sourceEntryCount},`,
      `'sha256:${v8Digest}',${entryCount},${rows.length},`)
    .replace(`ops.scac_mutation_registration_v8('sha256:${v7Seal.digest.slice("sha256:".length)}',`,
      `ops.scac_mutation_registration_v8('sha256:${v8Digest}',`)
    .replace("alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v6;",
      "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v7;")
    .replace("__SIEP17_V7_CATALOG_SUCCESSOR__", `${v7History}${v8Current}`);

  const v7CatalogJson = JSON.stringify(SIEP16_INTEGRATED_DB_CATALOG_BASELINE);
  const v8CatalogJson = JSON.stringify(dbCatalogBaseline);
  sql = sql
    .replace("check (registry_version in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v4','scac-mutation-registry.v5','scac-mutation-registry.v6','scac-mutation-registry.v8'))",
      "check (registry_version in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v4','scac-mutation-registry.v5','scac-mutation-registry.v6','scac-mutation-registry.v7','scac-mutation-registry.v8'))")
    .replace("if p_registry_version not in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v4','scac-mutation-registry.v5','scac-mutation-registry.v6') then return false; end if;",
      "if p_registry_version not in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v4','scac-mutation-registry.v5','scac-mutation-registry.v6','scac-mutation-registry.v7') then return false; end if;")
    .replace(`    when 'scac-mutation-registry.v6' then '${HISTORICAL_REGISTRY_SEALS.v6.digest}' end;`,
      `    when 'scac-mutation-registry.v6' then '${HISTORICAL_REGISTRY_SEALS.v6.digest}'\n    when '${v7Seal.version}' then '${v7Seal.digest}' end;`)
    .replace(`    when 'scac-mutation-registry.v6' then '${JSON.stringify(SIEP16_DB_CATALOG_BASELINE)}'::jsonb end;`,
      `    when 'scac-mutation-registry.v6' then '${JSON.stringify(SIEP16_DB_CATALOG_BASELINE)}'::jsonb\n    when '${v7Seal.version}' then '${v7CatalogJson}'::jsonb end;`)
    .replace(`    ('scac-mutation-registry.v6','${HISTORICAL_REGISTRY_SEALS.v6.digest}',${HISTORICAL_REGISTRY_SEALS.v6.entryCount},${HISTORICAL_REGISTRY_SEALS.v6.sourceEntryCount})\n`,
      `    ('scac-mutation-registry.v6','${HISTORICAL_REGISTRY_SEALS.v6.digest}',${HISTORICAL_REGISTRY_SEALS.v6.entryCount},${HISTORICAL_REGISTRY_SEALS.v6.sourceEntryCount}),\n    ('${v7Seal.version}','${v7Seal.digest}',${v7Seal.entryCount},${v7Seal.sourceEntryCount})\n`)
    .replace("    ops.scac_mutation_registry_v6_seal_available()) then",
      "    ops.scac_mutation_registry_v6_seal_available() and\n    ops.scac_mutation_registry_v7_seal_available()) then")
    .replace(`or (r.registry_version='scac-mutation-registry.v8' and r.registry_digest='${v7Seal.digest}')`,
      `or (r.registry_version='scac-mutation-registry.v7' and r.registry_digest='${v7Seal.digest}')\n         or (r.registry_version='scac-mutation-registry.v8' and r.registry_digest='sha256:${v8Digest}')`)
    .replace(`  (registry_version='scac-mutation-registry.v8' and registry_digest='${v7Seal.digest}')`,
      `  (registry_version='scac-mutation-registry.v7' and registry_digest='${v7Seal.digest}') or\n  (registry_version='scac-mutation-registry.v8' and registry_digest='sha256:${v8Digest}')`)
    .replace(`'{registry_digest}',to_jsonb('${v7Seal.digest}'::text)`,
      `'{registry_digest}',to_jsonb('sha256:${v8Digest}'::text)`)
    .replace("ops.scac_mutation_registry_v6_seal_available(),ops.scac_mutation_catalog_v8_current()",
      "ops.scac_mutation_registry_v6_seal_available(),ops.scac_mutation_catalog_v7_live_at_seal(),ops.scac_mutation_catalog_v7_current(),ops.scac_mutation_registry_v7_seal_available(),ops.scac_mutation_catalog_v8_current()")
    .replace("SIEP-16 integrated successor snapshot: current policy epochs bind mutation registry v8 while historical v2/v3/v4/v5/v6 epochs remain immutable.",
      "SIEP-17 successor snapshot: current policy epochs bind mutation registry v8 while historical v2/v3/v4/v5/v6/v7 epochs remain immutable.");
  sql = sql
    .replace(`(select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v8')<>${v7Seal.entryCount}`,
      `(select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v8')<>${entryCount}`)
    .replace(`if (select registry_digest from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v6')<>'${HISTORICAL_REGISTRY_SEALS.v6.digest}'
     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v6')<>${HISTORICAL_REGISTRY_SEALS.v6.entryCount} then raise exception 'sealed SCAC mutation registry v6 changed during successor creation'; end if;`,
      `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v7')<>'${v7Seal.digest}'
     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v7')<>${v7Seal.entryCount} then raise exception 'sealed SCAC mutation registry v7 changed during successor creation'; end if;`);
  const duplicateV6HistoryStart = sql.indexOf(
    "alter function ops.scac_mutation_catalog_v6_current() rename to scac_mutation_catalog_v6_live_at_seal;");
  const v7HistoryStart = sql.indexOf(
    "alter function ops.scac_mutation_catalog_v7_current() rename to scac_mutation_catalog_v7_live_at_seal;");
  if (duplicateV6HistoryStart < 0 || v7HistoryStart <= duplicateV6HistoryStart)
    throw new Error("generated SCAC v8 migration has no exact duplicate-v6 history boundary");
  sql = `${sql.slice(0, duplicateV6HistoryStart)}${sql.slice(v7HistoryStart)}`
    .replace("ops.scac_policy_epoch_snapshot(),ops.scac_policy_epoch_snapshot_v6(),",
      "ops.scac_policy_epoch_snapshot(),ops.scac_policy_epoch_snapshot_v6(),ops.scac_policy_epoch_snapshot_v7(),");

  const seedStartMarker = "with seed as (select value as contract from jsonb_array_elements(";
  const seedEndMarker = "::jsonb))\ninsert into ops.scac_mutation_registry_entry";
  const seedStart = sql.indexOf(seedStartMarker) + seedStartMarker.length;
  const seedEnd = sql.indexOf(seedEndMarker, seedStart);
  if (seedStart < seedStartMarker.length || seedEnd < seedStart)
    throw new Error("sealed SCAC v7 migration has no exact source-seed boundary");
  const seed = JSON.stringify(rows.map(row => ({ ...row, entry_digest: `sha256:${sha256(row)}` })));
  return `${sql.slice(0, seedStart)}${sqlLiteral(seed)}${sql.slice(seedEnd)}`;
}

export function renderSIEP18ForwardRegistrySql(rows = fullInventory(),
  dbCatalogBaseline = SIEP18_FORWARD_DB_CATALOG_BASELINE) {
  const { v8: v8Seal } = HISTORICAL_REGISTRY_SEALS;
  const v9Digest = registryDigestFor(REGISTRY_V9_VERSION, rows, dbCatalogBaseline);
  const catalogCount = dbCatalogBaseline.secdef_execute.count +
    dbCatalogBaseline.relation_dml.count + dbCatalogBaseline.column_dml.count;
  const entryCount = rows.length + catalogCount;
  const v8MigrationPath = "migrations/0466_siep17_forward_mutation_registry.sql";
  const v8RuntimePath = "mcp-server/src/scac-mutation-registry.v8.generated.js";
  const v8Migration = readFileSync(resolve(REPO_ROOT, v8MigrationPath), "utf8");
  for (const path of [v8MigrationPath, v8RuntimePath]) {
    const observed = sha256(readFileSync(resolve(REPO_ROOT, path), "utf8"));
    if (observed !== HISTORICAL_REGISTRY_ARTIFACT_SHA256[path])
      throw new Error(`sealed historical SCAC v8 artifact changed: ${path}: ${observed}`);
  }

  const predecessorCatalogPreflight =
`-- Exact disposable-DB predecessor receipt. Refuse before creating any v9 function.\n` +
`do $siep18_preflight$\n` +
`declare observed_count integer; observed_digest text;\n` +
`begin\n` +
`  with recursive connected(oid) as (select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci' and not other.rolsuper),\n` +
`  runtime_roles as (select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper),\n` +
`  functions as (select p.oid,n.nspname,p.proname,pg_get_function_identity_arguments(p.oid) args,p.prosecdef,p.prokind,p.provolatile,p.proparallel,p.proconfig,p.proacl,p.proowner from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname not in ('pg_catalog','information_schema') and p.prokind in ('f','p')),\n` +
`  capabilities as (select f.*,acl.grantee,acl.privilege_type,acl.is_grantable from functions f cross join lateral aclexplode(coalesce(f.proacl,acldefault('f',f.proowner))) acl),\n` +
`  observed as (select 'db-function-acl:'||nspname||'.'||proname||'('||args||'):'||coalesce(r.rolname,'public')||':execute' ingress_key,jsonb_build_object('ingress_key','db-function-acl:'||nspname||'.'||proname||'('||args||'):'||coalesce(r.rolname,'public')||':execute','ingress_kind','db_function_acl','signature',nspname||'.'||proname||'('||args||')','security_definer',prosecdef,'function_kind',prokind,'volatility',provolatile,'parallel',proparallel,'config',coalesce(to_jsonb(proconfig),'[]'::jsonb),'grantee',coalesce(r.rolname,'public'),'privilege','execute','grantable',is_grantable) row from capabilities c left join pg_roles r on r.oid=c.grantee where prosecdef and privilege_type='EXECUTE' and grantee<>proowner and (grantee=0 or r.oid in(select oid from runtime_roles)))\n` +
`  select count(*),'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(jsonb_agg(row order by ingress_key collate "C", ops.scac_canonical_json(row) collate "C"),'[]'::jsonb)),'UTF8'),'sha256'),'hex') into observed_count,observed_digest from observed;\n` +
`  if observed_count<>${SIEP18_PRE_V9_DB_CATALOG_BASELINE.secdef_execute.count} or observed_digest<>'${SIEP18_PRE_V9_DB_CATALOG_BASELINE.secdef_execute.digest}' then\n` +
`    raise exception 'SIEP-18 pre-v9 security-definer catalog receipt drifted: count %, digest %',observed_count,observed_digest;\n` +
`  end if;\n` +
`end $siep18_preflight$;\n\n`;

  const currentV8Marker = "create or replace function ops.scac_mutation_catalog_v8_current()";
  const policyMarker =
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v7;";
  const currentV8Start = v8Migration.indexOf(currentV8Marker);
  const secondCurrentV8 = v8Migration.indexOf(currentV8Marker, currentV8Start + currentV8Marker.length);
  const policyStart = v8Migration.indexOf(policyMarker);
  const secondPolicy = v8Migration.indexOf(policyMarker, policyStart + policyMarker.length);
  if (currentV8Start < 0 || secondCurrentV8 >= 0 || policyStart <= currentV8Start || secondPolicy >= 0)
    throw new Error("sealed SCAC v8 migration has no exact catalog successor boundary");
  const v8Current = v8Migration.slice(currentV8Start, policyStart);
  const v8History = `alter function ops.scac_mutation_catalog_v8_current() rename to scac_mutation_catalog_v8_live_at_seal;\n` +
`create or replace function ops.scac_mutation_registry_v8_seal_available()\n` +
`returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$\n` +
`  select ops.scac_mutation_registry_seal_valid('scac-mutation-registry.v8')\n` +
`$fn$;\n` +
`create or replace function ops.scac_mutation_catalog_v8_current()\n` +
`returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$\n` +
`  select ops.scac_mutation_catalog_v8_live_at_seal()\n` +
`$fn$;\n` +
`comment on function ops.scac_mutation_registry_v8_seal_available() is 'Exact immutable v8 registry seal; separate from whether the live catalog still equals v8.';\n` +
`comment on function ops.scac_mutation_catalog_v8_current() is 'Historical v8 live-catalog validator; expected to become false after the v9 authority surface is installed.';\n\n`;
  const v9Current = replaceExactlyOnce(
    v8Current
      .replaceAll("scac_mutation_catalog_v8_current", "scac_mutation_catalog_v9_current")
      .replaceAll("scac-mutation-registry.v8", "scac-mutation-registry.v9"),
    `if observed_count<>${SIEP17_FORWARD_DB_CATALOG_BASELINE.secdef_execute.count} or observed_digest<>'${SIEP17_FORWARD_DB_CATALOG_BASELINE.secdef_execute.digest}' then return false; end if;`,
    `if observed_count<>${dbCatalogBaseline.secdef_execute.count} or observed_digest<>'${dbCatalogBaseline.secdef_execute.digest}' then return false; end if;`,
    "SIEP-18 v9 current catalog baseline",
  );

  let sql = replaceExactlyOnce(v8Migration, v8Current,
    "__SIEP18_V8_CATALOG_SUCCESSOR__", "SIEP-18 v8 current catalog block");
  sql = replaceExactlyOnce(sql,
    "-- SIEP-17 / SCAC-07: forward-only mutation registry v8.",
    "-- SIEP-18 / SCAC-08: forward-only mutation registry v9 and exact reference-monitor grant binding.",
    "SIEP-18 migration header");
  // These broad rewrites operate only on a byte-for-byte sealed v8 artifact;
  // the predecessor hash checks above make their input finite and immutable.
  sql = sql
    .replaceAll("scac-mutation-registry.v8", "scac-mutation-registry.v9")
    .replaceAll("_v8", "_v9")
    .replaceAll(" v8", " v9");
  sql = replaceExactlyOnce(sql, JSON.stringify(SIEP17_FORWARD_DB_CATALOG_BASELINE),
    JSON.stringify(dbCatalogBaseline), "SIEP-18 v9 catalog projection");
  sql = replaceExactlyOnce(sql,
    `'${v8Seal.digest}',${v8Seal.entryCount},${v8Seal.sourceEntryCount},`,
    `'sha256:${v9Digest}',${entryCount},${rows.length},`,
    "SIEP-18 v9 registry row");
  sql = replaceExactlyOnce(sql,
    `ops.scac_mutation_registration_v9('${v8Seal.digest}',`,
    `ops.scac_mutation_registration_v9('sha256:${v9Digest}',`,
    "SIEP-18 v9 snapshot registry lookup");
  sql = replaceExactlyOnce(sql,
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v7;",
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v8;",
    "SIEP-18 policy snapshot predecessor");
  sql = replaceExactlyOnce(sql, "__SIEP18_V8_CATALOG_SUCCESSOR__",
    `${v8History}${v9Current}`, "SIEP-18 v8 catalog history insertion");

  const v8CatalogJson = JSON.stringify(SIEP17_FORWARD_DB_CATALOG_BASELINE);
  sql = replaceExactlyOnce(sql,
    "check (registry_version in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v4','scac-mutation-registry.v5','scac-mutation-registry.v6','scac-mutation-registry.v7','scac-mutation-registry.v9'))",
    "check (registry_version in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v4','scac-mutation-registry.v5','scac-mutation-registry.v6','scac-mutation-registry.v7','scac-mutation-registry.v8','scac-mutation-registry.v9'))",
    "SIEP-18 registry-version constraint");
  sql = replaceExactlyOnce(sql,
    "if p_registry_version not in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v4','scac-mutation-registry.v5','scac-mutation-registry.v6','scac-mutation-registry.v7') then return false; end if;",
    "if p_registry_version not in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v4','scac-mutation-registry.v5','scac-mutation-registry.v6','scac-mutation-registry.v7','scac-mutation-registry.v8') then return false; end if;",
    "SIEP-18 historical seal allowlist");
  sql = replaceExactlyOnce(sql,
    `    when 'scac-mutation-registry.v7' then '${HISTORICAL_REGISTRY_SEALS.v7.digest}' end;`,
    `    when 'scac-mutation-registry.v7' then '${HISTORICAL_REGISTRY_SEALS.v7.digest}'\n    when '${v8Seal.version}' then '${v8Seal.digest}' end;`,
    "SIEP-18 historical digest case");
  sql = replaceExactlyOnce(sql,
    `    when 'scac-mutation-registry.v7' then '${JSON.stringify(SIEP16_INTEGRATED_DB_CATALOG_BASELINE)}'::jsonb end;`,
    `    when 'scac-mutation-registry.v7' then '${JSON.stringify(SIEP16_INTEGRATED_DB_CATALOG_BASELINE)}'::jsonb\n    when '${v8Seal.version}' then '${v8CatalogJson}'::jsonb end;`,
    "SIEP-18 historical catalog case");
  sql = replaceExactlyOnce(sql,
    `    ('scac-mutation-registry.v7','${HISTORICAL_REGISTRY_SEALS.v7.digest}',${HISTORICAL_REGISTRY_SEALS.v7.entryCount},${HISTORICAL_REGISTRY_SEALS.v7.sourceEntryCount})\n`,
    `    ('scac-mutation-registry.v7','${HISTORICAL_REGISTRY_SEALS.v7.digest}',${HISTORICAL_REGISTRY_SEALS.v7.entryCount},${HISTORICAL_REGISTRY_SEALS.v7.sourceEntryCount}),\n    ('${v8Seal.version}','${v8Seal.digest}',${v8Seal.entryCount},${v8Seal.sourceEntryCount})\n`,
    "SIEP-18 historical seal tuple");
  sql = replaceExactlyOnce(sql,
    "    ops.scac_mutation_registry_v7_seal_available()) then",
    "    ops.scac_mutation_registry_v7_seal_available() and\n    ops.scac_mutation_registry_v8_seal_available()) then",
    "SIEP-18 snapshot predecessor seal");
  sql = replaceExactlyOnce(sql,
    `or (r.registry_version='scac-mutation-registry.v9' and r.registry_digest='${v8Seal.digest}')`,
    `or (r.registry_version='scac-mutation-registry.v8' and r.registry_digest='${v8Seal.digest}')\n         or (r.registry_version='scac-mutation-registry.v9' and r.registry_digest='sha256:${v9Digest}')`,
    "SIEP-18 epoch-chain digest cases");
  sql = replaceExactlyOnce(sql,
    `  (registry_version='scac-mutation-registry.v9' and registry_digest='${v8Seal.digest}')`,
    `  (registry_version='scac-mutation-registry.v8' and registry_digest='${v8Seal.digest}') or\n  (registry_version='scac-mutation-registry.v9' and registry_digest='sha256:${v9Digest}')`,
    "SIEP-18 epoch constraint digest cases");
  sql = replaceExactlyOnce(sql,
    `'{registry_digest}',to_jsonb('${v8Seal.digest}'::text)`,
    `'{registry_digest}',to_jsonb('sha256:${v9Digest}'::text)`,
    "SIEP-18 snapshot registry digest");
  sql = replaceExactlyOnce(sql,
    "ops.scac_mutation_registry_v7_seal_available(),ops.scac_mutation_catalog_v9_current()",
    "ops.scac_mutation_registry_v7_seal_available(),ops.scac_mutation_catalog_v8_live_at_seal(),ops.scac_mutation_catalog_v8_current(),ops.scac_mutation_registry_v8_seal_available(),ops.scac_mutation_catalog_v9_current()",
    "SIEP-18 historical function revoke list");
  sql = replaceExactlyOnce(sql,
    "SIEP-17 successor snapshot: current policy epochs bind mutation registry v9 while historical v2/v3/v4/v5/v6/v7 epochs remain immutable.",
    "SIEP-18 successor snapshot: current policy epochs bind mutation registry v9 while historical v2/v3/v4/v5/v6/v7/v8 epochs remain immutable.",
    "SIEP-18 policy snapshot comment");
  sql = replaceExactlyOnce(sql,
    `(select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v9')<>${v8Seal.entryCount}`,
    `(select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v9')<>${entryCount}`,
    "SIEP-18 v9 entry count guard");
  sql = replaceExactlyOnce(sql,
    `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v7')<>'${HISTORICAL_REGISTRY_SEALS.v7.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v7')<>${HISTORICAL_REGISTRY_SEALS.v7.entryCount} then raise exception 'sealed SCAC mutation registry v7 changed during successor creation'; end if;`,
    `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v8')<>'${v8Seal.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v8')<>${v8Seal.entryCount} then raise exception 'sealed SCAC mutation registry v8 changed during successor creation'; end if;`,
    "SIEP-18 predecessor seal guard");
  sql = replaceExactlyOnce(sql,
    "ops.scac_policy_epoch_snapshot(),ops.scac_policy_epoch_snapshot_v6(),ops.scac_policy_epoch_snapshot_v7(),",
    "ops.scac_policy_epoch_snapshot(),ops.scac_policy_epoch_snapshot_v6(),ops.scac_policy_epoch_snapshot_v7(),ops.scac_policy_epoch_snapshot_v8(),",
    "SIEP-18 historical policy snapshot revoke list");

  const duplicateV7HistoryMarker =
    "alter function ops.scac_mutation_catalog_v7_current() rename to scac_mutation_catalog_v7_live_at_seal;";
  const v8HistoryMarker =
    "alter function ops.scac_mutation_catalog_v8_current() rename to scac_mutation_catalog_v8_live_at_seal;";
  const duplicateV7HistoryStart = sql.indexOf(duplicateV7HistoryMarker);
  const secondDuplicateV7 = sql.indexOf(
    duplicateV7HistoryMarker, duplicateV7HistoryStart + duplicateV7HistoryMarker.length);
  const v8HistoryStart = sql.indexOf(v8HistoryMarker);
  const secondV8History = sql.indexOf(v8HistoryMarker, v8HistoryStart + v8HistoryMarker.length);
  if (duplicateV7HistoryStart < 0 || secondDuplicateV7 >= 0 ||
      v8HistoryStart <= duplicateV7HistoryStart || secondV8History >= 0)
    throw new Error("generated SCAC v9 migration has no exact duplicate-v7 history boundary");
  sql = `${sql.slice(0, duplicateV7HistoryStart)}${sql.slice(v8HistoryStart)}`;

  const seedStartMarker = "with seed as (select value as contract from jsonb_array_elements(";
  const seedEndMarker = "::jsonb))\ninsert into ops.scac_mutation_registry_entry";
  const seedStart = sql.indexOf(seedStartMarker);
  const secondSeedStart = sql.indexOf(seedStartMarker, seedStart + seedStartMarker.length);
  const seedEnd = sql.indexOf(seedEndMarker, seedStart + seedStartMarker.length);
  const secondSeedEnd = sql.indexOf(seedEndMarker, seedEnd + seedEndMarker.length);
  if (seedStart < 0 || secondSeedStart >= 0 || seedEnd < 0 || secondSeedEnd >= 0)
    throw new Error("sealed SCAC v8 migration has no exact source-seed boundary");
  const seed = JSON.stringify(rows.map(row => ({ ...row, entry_digest: `sha256:${sha256(row)}` })));
  sql = `${sql.slice(0, seedStart + seedStartMarker.length)}${sqlLiteral(seed)}${sql.slice(seedEnd)}`;

  const monitorMigration = readFileSync(
    resolve(REPO_ROOT, "migrations/0467_siep18_atomic_db_monitor_grants.sql"), "utf8");
  const monitorDigest = sha256(monitorMigration);
  if (monitorDigest !== SIEP18_MONITOR_ARTIFACT_SHA256)
    throw new Error(`reviewed SIEP-18 monitor artifact changed: ${monitorDigest}`);
  const monitorStartMarker = "create or replace function ops.scac_reference_monitor_state()";
  const monitorEndMarker = "create or replace function ops.scac_register_token_issuer_binding(";
  const monitorStart = monitorMigration.indexOf(monitorStartMarker);
  const secondMonitorStart = monitorMigration.indexOf(
    monitorStartMarker, monitorStart + monitorStartMarker.length);
  const monitorEnd = monitorMigration.indexOf(monitorEndMarker, monitorStart);
  const secondMonitorEnd = monitorMigration.indexOf(monitorEndMarker, monitorEnd + monitorEndMarker.length);
  if (monitorStart < 0 || secondMonitorStart >= 0 || monitorEnd <= monitorStart || secondMonitorEnd >= 0)
    throw new Error("SIEP-18 monitor migration has no exact state-function boundary");
  let monitorState = monitorMigration.slice(monitorStart, monitorEnd);
  monitorState = replaceExactlyOnce(monitorState,
    "        relation_digest text; column_digest text; grant_state text; guard_state text;",
    "        grant_state text; guard_state text;",
    "SIEP-18 monitor temporary digest declarations");
  monitorState = replaceExactlyOnce(monitorState,
    "  relation_digest:=registry.catalog_projection#>>'{relation_dml,digest}';\n  column_digest:=registry.catalog_projection#>>'{column_dml,digest}';\n",
    "", "SIEP-18 monitor temporary category digests");
  monitorState = replaceExactlyOnce(monitorState,
    "  grant_state:=case when registry.registry_version is not null and\n    (grant_snapshot->>'grant_digest')=ops.scac_reference_monitor_sha256(jsonb_build_array(\n      jsonb_build_object('relation_digest',relation_digest),\n      jsonb_build_object('column_digest',column_digest)))\n    then 'current' else 'measured_pending_v9_binding' end;\n  -- The v9 successor replaces the temporary combined-digest comparison above\n  -- with its exact catalog grant digest after 0467 is installed.\n",
    `  grant_state:=case when registry.registry_version='${REGISTRY_V9_VERSION}' and\n    (grant_snapshot->>'entry_count')::integer=${dbCatalogBaseline.runtime_dml_grants.count} and\n    grant_snapshot->>'grant_digest'='${dbCatalogBaseline.runtime_dml_grants.digest}'\n    then 'current' else 'drifted_or_unbound' end;\n`,
    "SIEP-18 exact runtime grant binding");
  return `${predecessorCatalogPreflight}${sql}\n-- Exact post-0467 grant binding; generated from disposable-DB readback.\n${monitorState}`
    .replace(/\n+$/, "\n");
}

export function renderSourceMergeForwardRegistrySql(rows = fullInventory(),
  dbCatalogBaseline = SOURCE_MERGE_FORWARD_DB_CATALOG_BASELINE) {
  const { v9: v9Seal } = HISTORICAL_REGISTRY_SEALS;
  const v10Digest = registryDigestFor(REGISTRY_V10_VERSION, rows, dbCatalogBaseline);
  const catalogCount = dbCatalogBaseline.secdef_execute.count +
    dbCatalogBaseline.relation_dml.count + dbCatalogBaseline.column_dml.count;
  const entryCount = rows.length + catalogCount;
  const v9MigrationPath = "migrations/0468_siep18_forward_mutation_registry.sql";
  const v9RuntimePath = "mcp-server/src/scac-mutation-registry.v9.generated.js";
  const v9Migration = readFileSync(resolve(REPO_ROOT, v9MigrationPath), "utf8");
  for (const path of [v9MigrationPath, v9RuntimePath]) {
    const observed = sha256(readFileSync(resolve(REPO_ROOT, path), "utf8"));
    if (observed !== HISTORICAL_REGISTRY_ARTIFACT_SHA256[path])
      throw new Error(`sealed historical SCAC v9 artifact changed: ${path}: ${observed}`);
  }

  const predecessorCatalogPreflight =
`-- Exact disposable-DB predecessor receipt. Refuse before creating any v10 function.\n` +
`do $source_merge_preflight$\n` +
`declare observed_count integer; observed_digest text;\n` +
`begin\n` +
`  with recursive connected(oid) as (select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci' and not other.rolsuper),\n` +
`  runtime_roles as (select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper),\n` +
`  functions as (select p.oid,n.nspname,p.proname,pg_get_function_identity_arguments(p.oid) args,p.prosecdef,p.prokind,p.provolatile,p.proparallel,p.proconfig,p.proacl,p.proowner from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname not in ('pg_catalog','information_schema') and p.prokind in ('f','p')),\n` +
`  capabilities as (select f.*,acl.grantee,acl.privilege_type,acl.is_grantable from functions f cross join lateral aclexplode(coalesce(f.proacl,acldefault('f',f.proowner))) acl),\n` +
`  observed as (select 'db-function-acl:'||nspname||'.'||proname||'('||args||'):'||coalesce(r.rolname,'public')||':execute' ingress_key,jsonb_build_object('ingress_key','db-function-acl:'||nspname||'.'||proname||'('||args||'):'||coalesce(r.rolname,'public')||':execute','ingress_kind','db_function_acl','signature',nspname||'.'||proname||'('||args||')','security_definer',prosecdef,'function_kind',prokind,'volatility',provolatile,'parallel',proparallel,'config',coalesce(to_jsonb(proconfig),'[]'::jsonb),'grantee',coalesce(r.rolname,'public'),'privilege','execute','grantable',is_grantable) row from capabilities c left join pg_roles r on r.oid=c.grantee where prosecdef and privilege_type='EXECUTE' and grantee<>proowner and (grantee=0 or r.oid in(select oid from runtime_roles)))\n` +
`  select count(*),'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(jsonb_agg(row order by ingress_key collate "C", ops.scac_canonical_json(row) collate "C"),'[]'::jsonb)),'UTF8'),'sha256'),'hex') into observed_count,observed_digest from observed;\n` +
`  if observed_count<>${SOURCE_MERGE_PRE_V10_DB_CATALOG_BASELINE.secdef_execute.count} or observed_digest<>'${SOURCE_MERGE_PRE_V10_DB_CATALOG_BASELINE.secdef_execute.digest}' then\n` +
`    raise exception 'source-merge pre-v10 security-definer catalog receipt drifted: count %, digest %',observed_count,observed_digest;\n` +
`  end if;\n` +
`end $source_merge_preflight$;\n\n`;

  const headerMarker = "-- SIEP-18 / SCAC-08: forward-only mutation registry v9 and exact reference-monitor grant binding.";
  const coreStart = v9Migration.indexOf(headerMarker);
  if (coreStart < 0 || v9Migration.indexOf(headerMarker, coreStart + headerMarker.length) >= 0)
    throw new Error("sealed SCAC v9 migration has no exact successor core boundary");
  const v9Core = v9Migration.slice(coreStart);
  const currentV9Marker = "create or replace function ops.scac_mutation_catalog_v9_current()";
  const policyMarker =
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v8;";
  const currentV9Start = v9Core.indexOf(currentV9Marker);
  const secondCurrentV9 = v9Core.indexOf(currentV9Marker, currentV9Start + currentV9Marker.length);
  const v8HistoryMarker =
    "alter function ops.scac_mutation_catalog_v8_current() rename to scac_mutation_catalog_v8_live_at_seal;";
  const v8HistoryStart = v9Core.indexOf(v8HistoryMarker);
  const secondV8History = v9Core.indexOf(v8HistoryMarker, v8HistoryStart + v8HistoryMarker.length);
  const policyStart = v9Core.indexOf(policyMarker);
  const secondPolicy = v9Core.indexOf(policyMarker, policyStart + policyMarker.length);
  if (v8HistoryStart < 0 || secondV8History >= 0 || currentV9Start <= v8HistoryStart ||
      secondCurrentV9 >= 0 || policyStart <= currentV9Start || secondPolicy >= 0)
    throw new Error("sealed SCAC v9 migration has no exact catalog successor boundary");
  // Migration 0468 already converted the v8 live-catalog predicate into historical
  // evidence. A v10 successor must preserve that installed function, not replay the
  // rename and collide with scac_mutation_catalog_v8_live_at_seal().
  const installedV8History = v9Core.slice(v8HistoryStart, currentV9Start);
  const v9Current = v9Core.slice(currentV9Start, policyStart);
  const v9History = `alter function ops.scac_mutation_catalog_v9_current() rename to scac_mutation_catalog_v9_live_at_seal;\n` +
`create or replace function ops.scac_mutation_registry_v9_seal_available()\n` +
`returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$\n` +
`  select ops.scac_mutation_registry_seal_valid('scac-mutation-registry.v9')\n` +
`$fn$;\n` +
`create or replace function ops.scac_mutation_catalog_v9_current()\n` +
`returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$\n` +
`  select ops.scac_mutation_catalog_v9_live_at_seal()\n` +
`$fn$;\n` +
`comment on function ops.scac_mutation_registry_v9_seal_available() is 'Exact immutable v9 registry seal; separate from whether the live catalog still equals v9.';\n` +
`comment on function ops.scac_mutation_catalog_v9_current() is 'Historical v9 live-catalog validator; expected to become false after the v10 authority surface is installed.';\n\n`;
  const v10Current = replaceExactlyOnce(
    v9Current
      .replaceAll("scac_mutation_catalog_v9_current", "scac_mutation_catalog_v10_current")
      .replaceAll("scac-mutation-registry.v9", "scac-mutation-registry.v10"),
    `if observed_count<>${SIEP18_FORWARD_DB_CATALOG_BASELINE.secdef_execute.count} or observed_digest<>'${SIEP18_FORWARD_DB_CATALOG_BASELINE.secdef_execute.digest}' then return false; end if;`,
    `if observed_count<>${dbCatalogBaseline.secdef_execute.count} or observed_digest<>'${dbCatalogBaseline.secdef_execute.digest}' then return false; end if;`,
    "source-merge v10 current catalog baseline",
  );

  let sql = replaceExactlyOnce(v9Core, v9Current,
    "__SOURCE_MERGE_V9_CATALOG_SUCCESSOR__", "source-merge v9 current catalog block");
  sql = replaceExactlyOnce(sql, installedV8History, "",
    "source-merge already-installed v8 catalog history");
  sql = replaceExactlyOnce(sql, headerMarker,
    "-- SCAC-09: forward-only mutation registry v10 after source-merge authority projection.",
    "source-merge migration header");
  sql = sql
    .replaceAll("scac-mutation-registry.v9", "scac-mutation-registry.v10")
    .replaceAll("_v9", "_v10")
    .replaceAll(" v9", " v10");
  sql = replaceExactlyOnce(sql, JSON.stringify(SIEP18_FORWARD_DB_CATALOG_BASELINE),
    JSON.stringify(dbCatalogBaseline), "source-merge v10 catalog projection");
  sql = replaceExactlyOnce(sql,
    `'${v9Seal.digest}',${v9Seal.entryCount},${v9Seal.sourceEntryCount},`,
    `'sha256:${v10Digest}',${entryCount},${rows.length},`,
    "source-merge v10 registry row");
  sql = replaceExactlyOnce(sql,
    `ops.scac_mutation_registration_v10('${v9Seal.digest}',`,
    `ops.scac_mutation_registration_v10('sha256:${v10Digest}',`,
    "source-merge v10 snapshot registry lookup");
  sql = replaceExactlyOnce(sql,
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v8;",
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v9;",
    "source-merge policy snapshot predecessor");
  sql = replaceExactlyOnce(sql, "__SOURCE_MERGE_V9_CATALOG_SUCCESSOR__",
    `${v9History}${v10Current}`, "source-merge v9 catalog history insertion");

  const v9CatalogJson = JSON.stringify(SIEP18_FORWARD_DB_CATALOG_BASELINE);
  sql = replaceExactlyOnce(sql,
    "check (registry_version in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v4','scac-mutation-registry.v5','scac-mutation-registry.v6','scac-mutation-registry.v7','scac-mutation-registry.v8','scac-mutation-registry.v10'))",
    "check (registry_version in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v4','scac-mutation-registry.v5','scac-mutation-registry.v6','scac-mutation-registry.v7','scac-mutation-registry.v8','scac-mutation-registry.v9','scac-mutation-registry.v10'))",
    "source-merge registry-version constraint");
  sql = replaceExactlyOnce(sql,
    "if p_registry_version not in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v4','scac-mutation-registry.v5','scac-mutation-registry.v6','scac-mutation-registry.v7','scac-mutation-registry.v8') then return false; end if;",
    "if p_registry_version not in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v4','scac-mutation-registry.v5','scac-mutation-registry.v6','scac-mutation-registry.v7','scac-mutation-registry.v8','scac-mutation-registry.v9') then return false; end if;",
    "source-merge historical seal allowlist");
  sql = replaceExactlyOnce(sql,
    `    when 'scac-mutation-registry.v8' then '${HISTORICAL_REGISTRY_SEALS.v8.digest}' end;`,
    `    when 'scac-mutation-registry.v8' then '${HISTORICAL_REGISTRY_SEALS.v8.digest}'\n    when '${v9Seal.version}' then '${v9Seal.digest}' end;`,
    "source-merge historical digest case");
  sql = replaceExactlyOnce(sql,
    `    when 'scac-mutation-registry.v8' then '${JSON.stringify(SIEP17_FORWARD_DB_CATALOG_BASELINE)}'::jsonb end;`,
    `    when 'scac-mutation-registry.v8' then '${JSON.stringify(SIEP17_FORWARD_DB_CATALOG_BASELINE)}'::jsonb\n    when '${v9Seal.version}' then '${v9CatalogJson}'::jsonb end;`,
    "source-merge historical catalog case");
  sql = replaceExactlyOnce(sql,
    `    ('scac-mutation-registry.v8','${HISTORICAL_REGISTRY_SEALS.v8.digest}',${HISTORICAL_REGISTRY_SEALS.v8.entryCount},${HISTORICAL_REGISTRY_SEALS.v8.sourceEntryCount})\n`,
    `    ('scac-mutation-registry.v8','${HISTORICAL_REGISTRY_SEALS.v8.digest}',${HISTORICAL_REGISTRY_SEALS.v8.entryCount},${HISTORICAL_REGISTRY_SEALS.v8.sourceEntryCount}),\n    ('${v9Seal.version}','${v9Seal.digest}',${v9Seal.entryCount},${v9Seal.sourceEntryCount})\n`,
    "source-merge historical seal tuple");
  sql = replaceExactlyOnce(sql,
    "    ops.scac_mutation_registry_v8_seal_available()) then",
    "    ops.scac_mutation_registry_v8_seal_available() and\n    ops.scac_mutation_registry_v9_seal_available()) then",
    "source-merge snapshot predecessor seal");
  sql = replaceExactlyOnce(sql,
    `or (r.registry_version='scac-mutation-registry.v10' and r.registry_digest='${v9Seal.digest}')`,
    `or (r.registry_version='scac-mutation-registry.v9' and r.registry_digest='${v9Seal.digest}')\n         or (r.registry_version='scac-mutation-registry.v10' and r.registry_digest='sha256:${v10Digest}')`,
    "source-merge epoch-chain digest cases");
  sql = replaceExactlyOnce(sql,
    `  (registry_version='scac-mutation-registry.v10' and registry_digest='${v9Seal.digest}')`,
    `  (registry_version='scac-mutation-registry.v9' and registry_digest='${v9Seal.digest}') or\n  (registry_version='scac-mutation-registry.v10' and registry_digest='sha256:${v10Digest}')`,
    "source-merge epoch constraint digest cases");
  sql = replaceExactlyOnce(sql,
    `'{registry_digest}',to_jsonb('${v9Seal.digest}'::text)`,
    `'{registry_digest}',to_jsonb('sha256:${v10Digest}'::text)`,
    "source-merge snapshot registry digest");
  sql = replaceExactlyOnce(sql,
    "ops.scac_mutation_registry_v8_seal_available(),ops.scac_mutation_catalog_v10_current()",
    "ops.scac_mutation_registry_v8_seal_available(),ops.scac_mutation_catalog_v9_live_at_seal(),ops.scac_mutation_catalog_v9_current(),ops.scac_mutation_registry_v9_seal_available(),ops.scac_mutation_catalog_v10_current()",
    "source-merge historical function revoke list");
  sql = replaceExactlyOnce(sql,
    "SIEP-18 successor snapshot: current policy epochs bind mutation registry v10 while historical v2/v3/v4/v5/v6/v7/v8 epochs remain immutable.",
    "Source-merge successor snapshot: current policy epochs bind mutation registry v10 while historical v2/v3/v4/v5/v6/v7/v8/v9 epochs remain immutable.",
    "source-merge policy snapshot comment");
  sql = replaceExactlyOnce(sql,
    `(select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v10')<>${v9Seal.entryCount}`,
    `(select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v10')<>${entryCount}`,
    "source-merge v10 entry count guard");
  sql = replaceExactlyOnce(sql,
    `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v8')<>'${HISTORICAL_REGISTRY_SEALS.v8.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v8')<>${HISTORICAL_REGISTRY_SEALS.v8.entryCount} then raise exception 'sealed SCAC mutation registry v8 changed during successor creation'; end if;`,
    `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v9')<>'${v9Seal.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v9')<>${v9Seal.entryCount} then raise exception 'sealed SCAC mutation registry v9 changed during successor creation'; end if;`,
    "source-merge predecessor seal guard");
  sql = replaceExactlyOnce(sql,
    "ops.scac_policy_epoch_snapshot(),ops.scac_policy_epoch_snapshot_v6(),ops.scac_policy_epoch_snapshot_v7(),ops.scac_policy_epoch_snapshot_v8(),",
    "ops.scac_policy_epoch_snapshot(),ops.scac_policy_epoch_snapshot_v6(),ops.scac_policy_epoch_snapshot_v7(),ops.scac_policy_epoch_snapshot_v8(),ops.scac_policy_epoch_snapshot_v9(),",
    "source-merge historical policy snapshot revoke list");

  const seedStartMarker = "with seed as (select value as contract from jsonb_array_elements(";
  const seedEndMarker = "::jsonb))\ninsert into ops.scac_mutation_registry_entry";
  const seedStart = sql.indexOf(seedStartMarker);
  const secondSeedStart = sql.indexOf(seedStartMarker, seedStart + seedStartMarker.length);
  const seedEnd = sql.indexOf(seedEndMarker, seedStart + seedStartMarker.length);
  const secondSeedEnd = sql.indexOf(seedEndMarker, seedEnd + seedEndMarker.length);
  if (seedStart < 0 || secondSeedStart >= 0 || seedEnd < 0 || secondSeedEnd >= 0)
    throw new Error("sealed SCAC v9 migration has no exact source-seed boundary");
  const seed = JSON.stringify(rows.map(row => ({ ...row, entry_digest: `sha256:${sha256(row)}` })));
  sql = `${sql.slice(0, seedStart + seedStartMarker.length)}${sqlLiteral(seed)}${sql.slice(seedEnd)}`;

  const controlSql = execFileSync("python3", [
    resolve(REPO_ROOT, "ops/sync_control_catalog.py"),
    "--render-control", "source_merge_eligibility",
  ], { cwd: REPO_ROOT, encoding: "utf8" });
  if (!controlSql.startsWith("-- GENERATED by ops/sync_control_catalog.py"))
    throw new Error("source-merge control catalog renderer returned noncanonical output");
  const mapBytes = readFileSync(resolve(REPO_ROOT, "ops/config/rule-enforcement-map.json"), "utf8");
  const mapDigest = sha256(mapBytes);
  const overlay = JSON.parse(readFileSync(
    resolve(REPO_ROOT, "ops/config/rule-delivery-activation-overlay.v1.json"), "utf8"));
  if (overlay.base_map_sha256 !== mapDigest || overlay.targets.length !== 8)
    throw new Error("source-merge rule-delivery overlay is not pinned to the exact current map");
  const targetTuples = overlay.targets.map(target =>
    `('${target.short_id}','${target.scope}','${target.pack}')`).join(",\n        ");
  const priorMapDigest = "f7bf5726d329dd240434e51f7401fac9a977a3fb710636738f379f60f565f904";
  const ruleMapRepinSql = mapDigest === priorMapDigest ? "" :
`-- The rule map changed; repin the unchanged exact eight delivery targets.\n` +
`do $rule_map_repin$\n` +
`declare updated bigint;\n` +
`begin\n` +
`  if (select count(*) from ops.rule_delivery_activation_target)<>8 or exists (\n` +
`    select 1 from ops.rule_delivery_activation_target t where\n` +
`      (t.short_id,t.expected_scope,t.expected_pack) not in (values\n        ${targetTuples})\n` +
`      or t.from_control<>'session_boot' or t.from_enforcement_class<>'surfacing'\n` +
`      or t.from_implementation_ref<>'hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js'\n` +
`      or t.from_test_ref<>'command:python3 hooks/gate-integrity.py --selftest'\n` +
`      or t.to_control<>'pack_delivery' or t.to_enforcement_class<>'stop_gate'\n` +
`      or t.to_implementation_ref<>'hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py'\n` +
`      or t.to_test_ref<>'ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py'\n` +
`      or t.map_digest<>'${priorMapDigest}') then\n` +
`    raise exception '0471 REFUSED: rule-delivery activation targets do not match the exact prior map preimage';\n` +
`  end if;\n` +
`  update ops.rule_delivery_activation_target set map_digest='${mapDigest}' where map_digest='${priorMapDigest}';\n` +
`  get diagnostics updated=row_count;\n` +
`  if updated<>8 then raise exception '0471 REFUSED: expected eight exact rule-delivery target repins, changed %',updated; end if;\n` +
`end $rule_map_repin$;\n`;
  return `${predecessorCatalogPreflight}${controlSql}\n${ruleMapRepinSql}${sql}`.replace(/\n+$/, "\n");
}

function renderMigration(rows = fullInventory()) {
  const digest = registryDigest(rows);
  const sourceCounts = Object.fromEntries(
    [...new Set(rows.map(row => row.ingress_kind))]
      .map(kind => [kind, rows.filter(row => row.ingress_kind === kind).length]),
  );
  const dbCatalogCount = DB_CATALOG_BASELINE.secdef_execute.count +
    DB_CATALOG_BASELINE.relation_dml.count + DB_CATALOG_BASELINE.column_dml.count;
  const totalCount = rows.length + dbCatalogCount;
  const seed = JSON.stringify(rows.map(row => ({
    ...row,
    entry_digest: `sha256:${sha256(row)}`,
  })));
  return `-- SIEP-11 / SCAC-01: immutable mutation ingress registry.\n` +
    `-- Source/test implementation only. Applying this migration to Production remains Joe-gated.\n` +
    `-- GENERATED seed from ops/scac-mutation-inventory.mjs; review, never hand-edit rows.\n\n` +
`create table ops.scac_mutation_registry_version (\n` +
`  registry_version text primary key check (registry_version='scac-mutation-registry.v1'),\n` +
`  program_key text not null check (program_key='carr-system-integrity-elimination-v1'),\n` +
`  package_key text not null check (package_key='11'),\n` +
`  charter_digest text not null check (charter_digest='sha256:473b7b1cd2ea975ba118f05406b35f4affdda0cb61f4487c252db129a882151c'),\n` +
`  registry_digest text not null unique check (registry_digest ~ '^sha256:[0-9a-f]{64}$'),\n` +
`  entry_count integer not null check (entry_count>0),\n` +
`  source_entry_count integer not null check (source_entry_count>0 and source_entry_count<=entry_count),\n` +
`  catalog_projection jsonb not null check (jsonb_typeof(catalog_projection)='object'),\n` +
`  entry_set_digest text check (entry_set_digest is null or entry_set_digest ~ '^sha256:[0-9a-f]{64}$'),\n` +
`  mcp_default_deny_source_guarded boolean not null,\n` +
`  db_metadata_authority boolean not null check (db_metadata_authority),\n` +
`  runtime_projection_authorizing boolean not null check (not runtime_projection_authorizing),\n` +
`  non_mcp_default_deny_operational boolean not null check (not non_mcp_default_deny_operational),\n` +
`  atomic_database_mediation_operational boolean not null check (not atomic_database_mediation_operational),\n` +
`  direct_database_grant_cutover boolean not null check (not direct_database_grant_cutover),\n` +
`  production_enforcement_active boolean not null check (not production_enforcement_active),\n` +
`  sealed_at timestamptz not null default now()\n` +
`);\n\n` +
`create table ops.scac_mutation_registry_entry (\n` +
`  registry_version text not null references ops.scac_mutation_registry_version(registry_version) on delete restrict,\n` +
`  ingress_key text not null check (ingress_key ~ '^[a-z][a-z0-9_-]+:' and ingress_key !~ E'[\\n\\r\\t]' and char_length(ingress_key)<=1000),\n` +
`  ingress_kind text not null check (ingress_kind in ('mcp_tool','worker_route','worker_sidewrite','db_function_acl','db_relation_acl','db_column_acl','job_definition','workflow_entrypoint','script_entrypoint','external_admin','break_glass')),\n` +
`  effect_class text not null check (effect_class in ('read_only','audit_side_effect','record_mutation','external_mutation','administrative_mutation','delegating','break_glass')),\n` +
`  source_locator text not null check (btrim(source_locator)<>'' and char_length(source_locator)<=500),\n` +
`  entry_digest text not null check (entry_digest ~ '^sha256:[0-9a-f]{64}$'),\n` +
`  contract jsonb not null check (jsonb_typeof(contract)='object'),\n` +
`  registered_at timestamptz not null default now(),\n` +
`  primary key (registry_version,ingress_key),\n` +
`  unique (registry_version,entry_digest),\n` +
`  check (contract->>'ingress_key'=ingress_key and contract->>'ingress_kind'=ingress_kind\n` +
`    and contract->>'effect_class'=effect_class and contract->>'source_locator'=source_locator\n` +
`    and contract->>'entry_digest' is null)\n` +
`);\n\n` +
`create or replace function ops.scac_canonical_json(p_value jsonb) returns text\n` +
`language plpgsql immutable strict set search_path=pg_catalog,ops as $$\n` +
`declare kind text:=jsonb_typeof(p_value); rendered text;\n` +
`begin\n` +
`  if kind='object' then\n` +
`    select '{'||coalesce(string_agg(to_jsonb(key)::text||':'||ops.scac_canonical_json(value),',' order by key collate "C"),'')||'}' into rendered from jsonb_each(p_value);\n` +
`    return rendered;\n` +
`  elsif kind='array' then\n` +
`    select '['||coalesce(string_agg(ops.scac_canonical_json(value),',' order by ordinal),'')||']' into rendered from jsonb_array_elements(p_value) with ordinality as a(value,ordinal);\n` +
`    return rendered;\n` +
`  end if;\n` +
`  return p_value::text;\n` +
`end $$;\n\n` +
`create or replace function ops.scac_mutation_registry_append_only() returns trigger\n` +
`language plpgsql security definer set search_path=pg_catalog,ops as $$\n` +
`begin raise exception 'SCAC mutation registry is append-only and sealed'; end $$;\n` +
`insert into ops.scac_mutation_registry_version(registry_version,program_key,package_key,charter_digest,registry_digest,entry_count,source_entry_count,catalog_projection,mcp_default_deny_source_guarded,db_metadata_authority,runtime_projection_authorizing,non_mcp_default_deny_operational,atomic_database_mediation_operational,direct_database_grant_cutover,production_enforcement_active)\n` +
`values ('scac-mutation-registry.v1','carr-system-integrity-elimination-v1','11','sha256:473b7b1cd2ea975ba118f05406b35f4affdda0cb61f4487c252db129a882151c','sha256:${digest}',${totalCount},${rows.length},${sqlLiteral(JSON.stringify(DB_CATALOG_BASELINE))}::jsonb,true,true,false,false,false,false,false);\n\n` +
`with seed as (select value as contract from jsonb_array_elements(${sqlLiteral(seed)}::jsonb))\n` +
`insert into ops.scac_mutation_registry_entry(registry_version,ingress_key,ingress_kind,effect_class,source_locator,entry_digest,contract)\n` +
`select 'scac-mutation-registry.v1',contract->>'ingress_key',contract->>'ingress_kind',contract->>'effect_class',\n` +
`       contract->>'source_locator','sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(contract-'entry_digest'),'UTF8'),'sha256'),'hex'),contract-'entry_digest'\n` +
`from seed;\n\n` +
`create or replace function ops.scac_mutation_registration(p_expected_digest text,p_ingress_key text)\n` +
`returns jsonb language plpgsql stable security definer set search_path=pg_catalog,ops as $$\n` +
`declare v ops.scac_mutation_registry_version%rowtype; e ops.scac_mutation_registry_entry%rowtype; actual_count integer; actual_set_digest text; contract_digest_mismatch boolean;\n` +
`begin\n` +
`  select * into v from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v1';\n` +
`  if v.registry_version is null then return jsonb_build_object('registered',false,'reason','registry_unavailable'); end if;\n` +
`  select count(*),'sha256:'||encode(public.digest(convert_to(coalesce(string_agg(entry_digest,',' order by ingress_key collate "C", entry_digest collate "C"),''),'UTF8'),'sha256'),'hex'),\n` +
`         coalesce(bool_or(entry_digest is distinct from 'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(contract),'UTF8'),'sha256'),'hex')),false)\n` +
`    into actual_count,actual_set_digest,contract_digest_mismatch from ops.scac_mutation_registry_entry where registry_version=v.registry_version;\n` +
`  if actual_count<>v.entry_count or actual_set_digest is distinct from v.entry_set_digest or contract_digest_mismatch then\n` +
`    return jsonb_build_object('registered',false,'reason','registry_corrupt','registry_version',v.registry_version,'registry_digest',v.registry_digest); end if;\n` +
`  if p_expected_digest is distinct from v.registry_digest then return jsonb_build_object('registered',false,'reason','digest_mismatch','registry_version',v.registry_version,'registry_digest',v.registry_digest); end if;\n` +
`  if p_ingress_key is null or p_ingress_key !~ '^[a-z][a-z0-9_-]+:' or p_ingress_key ~ E'[\\n\\r\\t]' or char_length(p_ingress_key)>1000 then return jsonb_build_object('registered',false,'reason','malformed_ingress','registry_version',v.registry_version,'registry_digest',v.registry_digest); end if;\n` +
`  select * into e from ops.scac_mutation_registry_entry where registry_version=v.registry_version and ingress_key=p_ingress_key;\n` +
`  if e.ingress_key is null then return jsonb_build_object('registered',false,'reason','unknown_ingress','registry_version',v.registry_version,'registry_digest',v.registry_digest); end if;\n` +
`  return jsonb_build_object('registered',true,'reason','registered_inventory','registry_version',v.registry_version,'registry_digest',v.registry_digest,\n` +
`    'ingress_key',e.ingress_key,'ingress_kind',e.ingress_kind,'effect_class',e.effect_class,'entry_digest',e.entry_digest,\n` +
`    'implementation_state',e.contract->>'implementation_state','atomic_database_mediation_operational',false);\n` +
`end $$;\n\n` +
`revoke all on ops.scac_mutation_registry_version,ops.scac_mutation_registry_entry from public,carr_reader,carr_writer,carr_jobs,carr_authority;\n` +
`revoke all on function ops.scac_canonical_json(jsonb) from public,carr_reader,carr_writer,carr_jobs,carr_authority;\n` +
`revoke all on function ops.scac_mutation_registry_append_only() from public,carr_reader,carr_writer,carr_jobs,carr_authority;\n` +
`revoke all on function ops.scac_mutation_registration(text,text) from public,carr_reader,carr_writer,carr_jobs,carr_authority;\n` +
`grant execute on function ops.scac_mutation_registration(text,text) to carr_reader,carr_writer,carr_jobs,carr_authority;\n` +
`comment on function ops.scac_mutation_registration(text,text) is 'Read-only SIEP-11 registry lookup. Presence inventories current ingress only; it never grants SCAC authority or claims SIEP-18 atomic mediation.';\n\n` +
catalogSeedSql() +
`do $$ declare actual_count integer; actual_digest text; expected jsonb; category text; kind text;\n` +
`begin\n` +
`  for category,kind in values ('secdef_execute','db_function_acl'),('relation_dml','db_relation_acl'),('column_dml','db_column_acl') loop\n` +
`    select count(*),'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(jsonb_agg(\n` +
`      contract-'effect_class'-'owner_package'-'implementation_state'-'classification_authorizing'-'source_locator' order by ingress_key collate "C", ops.scac_canonical_json(contract-'effect_class'-'owner_package'-'implementation_state'-'classification_authorizing'-'source_locator') collate "C"),'[]'::jsonb)),'UTF8'),'sha256'),'hex')\n` +
`      into actual_count,actual_digest from ops.scac_mutation_registry_entry where ingress_kind=kind;\n` +
`    select catalog_projection->category into expected from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v1';\n` +
`    if actual_count<>(expected->>'count')::integer or actual_digest<>expected->>'digest' then\n` +
`      raise exception 'SCAC database catalog category % drifted: count %, digest %',category,actual_count,actual_digest;\n` +
`    end if;\n` +
`  end loop;\n` +
`end $$;\n\n` +
`update ops.scac_mutation_registry_version v set entry_set_digest=(\n` +
`  select 'sha256:'||encode(public.digest(convert_to(string_agg(e.entry_digest,',' order by e.ingress_key collate "C"),'UTF8'),'sha256'),'hex')\n` +
`  from ops.scac_mutation_registry_entry e where e.registry_version=v.registry_version\n` +
`) where registry_version='scac-mutation-registry.v1';\n\n` +
`do $$ begin\n` +
`  if (select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v1')<>${totalCount}\n` +
`     or (select count(*) from ops.scac_mutation_registry_entry where ingress_kind='mcp_tool')<>${sourceCounts.mcp_tool}\n` +
`     or (select count(*) from ops.scac_mutation_registry_entry where ingress_kind='job_definition')<>${sourceCounts.job_definition}\n` +
`     or (select count(*) from ops.scac_mutation_registry_entry where ingress_kind='workflow_entrypoint')<>${sourceCounts.workflow_entrypoint}\n` +
`     or (select count(*) from ops.scac_mutation_registry_entry where ingress_kind='db_function_acl')<>${DB_CATALOG_BASELINE.secdef_execute.count}\n` +
`     or (select count(*) from ops.scac_mutation_registry_entry where ingress_kind='db_relation_acl')<>${DB_CATALOG_BASELINE.relation_dml.count}\n` +
`     or (select count(*) from ops.scac_mutation_registry_entry where ingress_kind='db_column_acl')<>${DB_CATALOG_BASELINE.column_dml.count}\n` +
`     or exists(select 1 from ops.scac_mutation_registry_entry where contract->>'owner_package'<>'11' or (contract->>'classification_authorizing')::boolean)\n` +
`     or exists(select 1 from ops.scac_mutation_registry_entry where entry_digest is distinct from 'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(contract),'UTF8'),'sha256'),'hex'))\n` +
`     or (select entry_set_digest is null from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v1') then\n` +
`    raise exception 'SCAC mutation registry seed is incomplete, drifted, or authority-expanding';\n` +
`  end if;\n` +
`end $$;\n` +
`alter table ops.scac_mutation_registry_version alter column entry_set_digest set not null;\n\n` +
`create trigger scac_mutation_registry_version_sealed before insert or update or delete on ops.scac_mutation_registry_version\n` +
`for each row execute function ops.scac_mutation_registry_append_only();\n` +
`create trigger scac_mutation_registry_entry_sealed before insert or update or delete on ops.scac_mutation_registry_entry\n` +
`for each row execute function ops.scac_mutation_registry_append_only();\n`;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const rows = fullInventory();
  const rebasedRuntimeModes = {
    "--write-rebased-runtime-v1": [REGISTRY_VERSION, DB_CATALOG_BASELINE, "mcp-server/src/scac-mutation-registry.generated.js"],
    "--write-rebased-runtime-v2": [REGISTRY_V2_VERSION, SIEP12_DB_CATALOG_BASELINE, "mcp-server/src/scac-mutation-registry.v2.generated.js"],
    "--write-rebased-runtime-v3": [REGISTRY_V3_VERSION, SIEP13_DB_CATALOG_BASELINE, "mcp-server/src/scac-mutation-registry.v3.generated.js"],
    "--write-rebased-runtime-v4": [REGISTRY_V4_VERSION, SIEP14_DB_CATALOG_BASELINE, "mcp-server/src/scac-mutation-registry.v4.generated.js"],
    "--write-rebased-runtime-v5": [REGISTRY_V5_VERSION, SIEP15_DB_CATALOG_BASELINE, "mcp-server/src/scac-mutation-registry.v5.generated.js"],
    "--write-rebased-runtime-v6": [REGISTRY_V6_VERSION, SIEP16_DB_CATALOG_BASELINE, "mcp-server/src/scac-mutation-registry.v6.generated.js"],
    "--write-rebased-runtime-v8": [REGISTRY_V8_VERSION, SIEP17_FORWARD_DB_CATALOG_BASELINE, "mcp-server/src/scac-mutation-registry.v8.generated.js"],
  };
  const rebasedMigrationModes = {
    "--write-rebased-migration-v1": [renderMigration, "migrations/0454_siep11_mutation_registry.sql"],
    "--write-rebased-migration-v3": [renderSIEP13RegistrySql, "migrations/0457_siep13_forward_mutation_registry.sql"],
    "--write-rebased-migration-v4": [renderSIEP14RegistrySql, "migrations/0459_siep14_forward_mutation_registry.sql"],
    "--write-rebased-migration-v5": [renderSIEP15RegistrySql, "migrations/0461_siep15_forward_mutation_registry.sql"],
    "--write-rebased-migration-v6": [renderSIEP16RegistrySql, "migrations/0462_siep16_forward_mutation_registry.sql"],
    "--write-rebased-migration-v8": [renderSIEP17ForwardRegistrySql, "migrations/0466_siep17_forward_mutation_registry.sql"],
  };
  if (rebasedRuntimeModes[process.argv[2]]) {
    const [version, dbCatalogBaseline, defaultTarget] = rebasedRuntimeModes[process.argv[2]];
    const target = resolve(process.argv[3] || defaultTarget);
    await writeFile(target, renderRuntimeProjection(rows, { version, dbCatalogBaseline }));
    process.stdout.write(`${target}\n`);
  } else if (rebasedMigrationModes[process.argv[2]]) {
    const [render, defaultTarget] = rebasedMigrationModes[process.argv[2]];
    const target = resolve(process.argv[3] || defaultTarget);
    await writeFile(target, render(rows));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-rebased-migration-v2") {
    const target = resolve(process.argv[3] || "migrations/0455_siep12_policy_epoch.sql");
    const v1Seal = registrySeal(REGISTRY_VERSION, rows, DB_CATALOG_BASELINE);
    await writeFile(target, renderPolicyEpochMigration(renderSuccessorRegistrySql(rows), {
      v1Seal,
      dbCatalogBaseline: SIEP12_DB_CATALOG_BASELINE,
    }));
    process.stdout.write(`${target}\n`);
  } else if (["--write-runtime", "--write-runtime-v2", "--write-runtime-v3", "--write-runtime-v4", "--write-runtime-v5", "--write-runtime-v6", "--write-runtime-v8",
    "--write-migration", "--write-siep12-migration", "--write-siep13-registry-migration",
    "--write-siep14-registry-migration", "--write-siep15-registry-migration",
    "--write-siep16-registry-migration", "--write-siep17-forward-registry-migration"].includes(process.argv[2])) {
    throw new Error(`${process.argv[2]} refused: SCAC registry v1-v8 artifacts are sealed historical evidence; create a forward successor instead`);
  } else if (process.argv[2] === "--write-runtime-v7") {
    const target = resolve(process.argv[3] || "mcp-server/src/scac-mutation-registry.v7.generated.js");
    await writeFile(target, renderRuntimeProjection(rows, {
      version: REGISTRY_V7_VERSION,
      dbCatalogBaseline: SIEP16_INTEGRATED_DB_CATALOG_BASELINE,
    }));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-siep16-integrated-registry-migration") {
    const target = resolve(process.argv[3] || "migrations/0464_siep16_integrated_mutation_registry.sql");
    await writeFile(target, renderSIEP16IntegratedRegistrySql(rows));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-runtime-v9") {
    const target = resolve(process.argv[3] || "mcp-server/src/scac-mutation-registry.v9.generated.js");
    await writeFile(target, renderRuntimeProjection(rows, {
      version: REGISTRY_V9_VERSION,
      dbCatalogBaseline: SIEP18_FORWARD_DB_CATALOG_BASELINE,
    }));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-siep18-forward-registry-migration") {
    const target = resolve(process.argv[3] || "migrations/0468_siep18_forward_mutation_registry.sql");
    await writeFile(target, renderSIEP18ForwardRegistrySql(rows));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-runtime-v10") {
    const target = resolve(process.argv[3] || "mcp-server/src/scac-mutation-registry.v10.generated.js");
    await writeFile(target, renderRuntimeProjection(rows, {
      version: REGISTRY_V10_VERSION,
      dbCatalogBaseline: SOURCE_MERGE_FORWARD_DB_CATALOG_BASELINE,
    }));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-source-merge-forward-registry-migration") {
    const target = resolve(process.argv[3] || "migrations/0471_source_merge_catalog_registry_successor.sql");
    await writeFile(target, renderSourceMergeForwardRegistrySql(rows));
    process.stdout.write(`${target}\n`);
  } else {
    process.stdout.write(`${JSON.stringify({ schema_version: REGISTRY_VERSION, digest: registryDigest(rows), rows }, null, 2)}\n`);
  }
}
