#!/usr/bin/env node

import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { renderPolicyEpochMigration } from "./scac-policy-epoch-sql.mjs";

let defaultTools;

async function loadDefaultTools() {
  if (!defaultTools) ({ TOOLS: defaultTools } = await import("../mcp-server/src/tools.js"));
  return defaultTools;
}

function requireTools(tools) {
  if (!tools)
    throw new Error("live MCP tools inventory is not loaded; pass TOOLS explicitly");
  return tools;
}

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
// v11 binds the exact post-0480 disposable-Postgres catalog receipt.
export const REGISTRY_V11_VERSION = "scac-mutation-registry.v11";
// v12 binds the exact post-0485 disposable-Postgres catalog receipt.
export const REGISTRY_V12_VERSION = "scac-mutation-registry.v12";
// v13 binds the reviewed Claude startup cursor repair after the v12 seal.
export const REGISTRY_V13_VERSION = "scac-mutation-registry.v13";
// v14 binds the Claude recovery actor-hydration repair after the v13 seal.
export const REGISTRY_V14_VERSION = "scac-mutation-registry.v14";
// v15 binds the Claude continuity config-preservation repair after the v14 seal.
export const REGISTRY_V15_VERSION = "scac-mutation-registry.v15";
// v16 binds the Codex post-compaction checkpoint refresh after the v15 seal.
export const REGISTRY_V16_VERSION = "scac-mutation-registry.v16";
// v17 registers the snapshot-guarded backup and artifact-status helper ingresses.
export const REGISTRY_V17_VERSION = "scac-mutation-registry.v17";
// v18 binds the WR-000068 sourced shape forward-correction surface after the v17 seal.
export const REGISTRY_V18_VERSION = "scac-mutation-registry.v18";
// v19 binds WR-000069's incident/work-request evidence edge after the v18 seal.
export const REGISTRY_V19_VERSION = "scac-mutation-registry.v19";
// v20 binds the continuity archive successor after the final v19 seal.
export const REGISTRY_V20_VERSION = "scac-mutation-registry.v20";
// v21 binds the R06 hooks-correctness re-digest after the final v20 seal.
export const REGISTRY_V21_VERSION = "scac-mutation-registry.v21";
// v22 binds the DoctorCRE v5 portfolio hierarchy after the final v21 seal.
export const REGISTRY_V22_VERSION = "scac-mutation-registry.v22";
export const REGISTRY_V23_VERSION = "scac-mutation-registry.v23";
const REPO_ROOT = fileURLToPath(new URL("../", import.meta.url));
const SOURCE_INVENTORY_FIXTURE_PATH = new URL(
  "./config/scac-registry-source-inventory-fixtures.v1.json", import.meta.url);
const SOURCE_INVENTORY_FIXTURES = JSON.parse(
  readFileSync(SOURCE_INVENTORY_FIXTURE_PATH, "utf8"));
const DIRECT_MIGRATION_PREIMAGE_PATH = new URL(
  "./config/scac-direct-registry-migration-preimages.v1.json", import.meta.url);
const DIRECT_MIGRATION_PREIMAGES = JSON.parse(
  readFileSync(DIRECT_MIGRATION_PREIMAGE_PATH, "utf8"));
const STATIC_FRONTIER_PREIMAGE_PATH = new URL(
  "./config/scac-static-frontier-migration-preimages.v1.json", import.meta.url);
const STATIC_FRONTIER_PREIMAGES = JSON.parse(
  readFileSync(STATIC_FRONTIER_PREIMAGE_PATH, "utf8"));
const FULL_ENTRY_SET_SEALS_PATH = new URL(
  "./config/scac-registry-full-entry-set-seals.json", import.meta.url);
// v1-v9 remain source-only until Joe approves Production application. Their
// active post-main tail may be regenerated only through the explicit
// --write-rebased-* commands below; ordinary historical write modes stay
// refused so an accidental invocation cannot rewrite a reviewed seal.
export const HISTORICAL_REGISTRY_SEALS = Object.freeze({
  v1: Object.freeze({ version: REGISTRY_VERSION, digest: "sha256:7cc2feacec82bf7cce2af9af309dc4ae9426922003471703af010f6728957190", entryCount: 1387, sourceEntryCount: 800 }),
  v2: Object.freeze({ version: REGISTRY_V2_VERSION, digest: "sha256:6dc9f8353712e0f9ee9dcbc96d05b802631e420cfa698944c2b6401a11c6a9ff", entryCount: 1391, sourceEntryCount: 800 }),
  v3: Object.freeze({ version: REGISTRY_V3_VERSION, digest: "sha256:ace611250aaf2ffd7b96ca3195d20e9dc2697bbea555eadc7c661407e838cae7", entryCount: 1395, sourceEntryCount: 800 }),
  v4: Object.freeze({ version: REGISTRY_V4_VERSION, digest: "sha256:32b9edaf4e718cfa55f87cc6b650f97480a09f00fbde35c10f1e5e23bc6eaa4c", entryCount: 1399, sourceEntryCount: 800 }),
  v5: Object.freeze({ version: REGISTRY_V5_VERSION, digest: "sha256:314a4b108eaded1a00b7f604af93d44838b615fab79820d1a2c9d9d4b08fa6ca", entryCount: 1404, sourceEntryCount: 800 }),
  v6: Object.freeze({ version: REGISTRY_V6_VERSION, digest: "sha256:afd27c13b68423dcaaeafc68c0c1ddc018452ef8d9e7cad5bb32cac4327d80d7", entryCount: 1408, sourceEntryCount: 800 }),
  v7: Object.freeze({ version: REGISTRY_V7_VERSION, digest: "sha256:405a1bd7aa421b16ebd3d3fe7422a2525826dba563dffeb2dad5a6b90d12859b", entryCount: 1412, sourceEntryCount: 800 }),
  v8: Object.freeze({ version: REGISTRY_V8_VERSION, digest: "sha256:56edcf25393a1c7b47985b9e2a272a8e7fcf0930709147ae823ca9b15290212e", entryCount: 1425, sourceEntryCount: 800 }),
  v9: Object.freeze({ version: REGISTRY_V9_VERSION, digest: "sha256:398ddf5f86a46110518f7498af63782b65e047b9f624b798bf3c798058a79b4d", entryCount: 1439, sourceEntryCount: 800 }),
  v10: Object.freeze({ version: REGISTRY_V10_VERSION, digest: "sha256:9109437968d4ab76090980ff9ec370da1ccfbb65e0683f485e0239d3d5158a62", entryCount: 1458, sourceEntryCount: 814 }),
  v11: Object.freeze({ version: REGISTRY_V11_VERSION, digest: "sha256:27d615d0a07d519b8e902a3a21b53918a314cfefdadfe5a50e2c4da33b2f9ad7", entryCount: 1471, sourceEntryCount: 819 }),
  v12: Object.freeze({ version: REGISTRY_V12_VERSION, digest: "sha256:4e8ae7fc6a017d7d3cd55452fbe4da7a302b93b8cfb34a43ba018dcf2b84f2a6", entryCount: 1487, sourceEntryCount: 825 }),
  v13: Object.freeze({ version: REGISTRY_V13_VERSION, digest: "sha256:7b2270375fe6a83d04dd3c62146db54321183d8ca202ee909e050663d2a050b8", entryCount: 1491, sourceEntryCount: 825 }),
  v14: Object.freeze({ version: REGISTRY_V14_VERSION, digest: "sha256:7f2987fe1dcb5bdf5bcbc269f9714261166419b992dc40f6fc446d6889e18558", entryCount: 1495, sourceEntryCount: 825 }),
  v15: Object.freeze({ version: REGISTRY_V15_VERSION, digest: "sha256:5f81f4579cf584a1807715f68b8297ddc4a5997a2c20906ef5300672d195360f", entryCount: 1499, sourceEntryCount: 825 }),
  v16: Object.freeze({ version: REGISTRY_V16_VERSION, digest: "sha256:d5418b025506b131252ddb214d75c2e1f995235db8b72ac56765485ccb5a1a54", entryCount: 1503, sourceEntryCount: 825 }),
  v17: Object.freeze({ version: REGISTRY_V17_VERSION, digest: "sha256:5aab15679a2d26207210bde3e16be265301b9c69816e08dc90b2f2e8a48c7db2", entryCount: 1509, sourceEntryCount: 827 }),
  v18: Object.freeze({ version: REGISTRY_V18_VERSION, digest: "sha256:680d42c68be736fe3f227019e3a4afd3e0aad53ed63d115db1fbb0467ea884c8", entryCount: 1515, sourceEntryCount: 827 }),
  v19: Object.freeze({ version: REGISTRY_V19_VERSION, digest: "sha256:19c1c9967bf960a64cefa39c53f6011193180f0c65128a1d8d5987ea6e120841", entryCount: 1520, sourceEntryCount: 828 }),
  v20: Object.freeze({ version: REGISTRY_V20_VERSION, digest: "sha256:45bf7a56d2756337c1b5efdad195f4935259fad6cf5f6a9c081c28592bacfb05", entryCount: 1524, sourceEntryCount: 828 }),
  v21: Object.freeze({ version: REGISTRY_V21_VERSION, digest: "sha256:d9100082d444f090e062a2fb9ac55043d0c9790c2fbd9b68672987e3ed12927b", entryCount: 1528, sourceEntryCount: 828 }),
  v22: Object.freeze({ version: REGISTRY_V22_VERSION, digest: "sha256:5bbe68942f2652523b52c024c07615b1718b59c7de4c0e41524a955566ba5f75", entryCount: 1590, sourceEntryCount: 833 }),
});
export const HISTORICAL_REGISTRY_ARTIFACT_SHA256 = Object.freeze({
  "migrations/0454_siep11_mutation_registry.sql": "7985d42b9b36964b33503f4ff42d332e6bcce085217f06464a9d6abf58126bdd",
  "migrations/0455_siep12_policy_epoch.sql": "8a2e223cf1c3637ad2b8d8b2fcac54f6407f9e2ceef9f7b227c502b13dc04101",
  "migrations/0457_siep13_forward_mutation_registry.sql": "32e85f50dcb95909db2e85d8eb41ff656cd642899d4e356f926c9edf39b0b007",
  "migrations/0459_siep14_forward_mutation_registry.sql": "1419b2c502583e09d1cabf26ad73b9861fc88e98d5616bb718949718a3253568",
  "mcp-server/src/scac-mutation-registry.generated.js": "e8cf336806337ba0ba25532816692ac2a24b48f9df58cee2966baaeafdae5abc",
  "mcp-server/src/scac-mutation-registry.v2.generated.js": "8fff96eb365b2e52882b89dcdfd2955e39e4a2caf3f1868f6edb93f2a72edb32",
  "mcp-server/src/scac-mutation-registry.v3.generated.js": "88ae034dcffb5c108efd98a5b1ca93798f712b13ad2615be7949af517ee26a38",
  "mcp-server/src/scac-mutation-registry.v4.generated.js": "6a0dba5ce1781d806dc14520d667eef71bdffa8899504ce929efe788a70a9f46",
  "migrations/0461_siep15_forward_mutation_registry.sql": "248fc0aa91e2bddf6d886131808b82d1ee20a488d581beef567bb6d0af36f867",
  "mcp-server/src/scac-mutation-registry.v5.generated.js": "fbc4957e875afaafcda88f1c21aa982eb52b91448bd608fa5d595a5ca6e848cc",
  "migrations/0462_siep16_forward_mutation_registry.sql": "a2ee709e37ff09dd7ab25f5fe7b407b5dbece93a701b86af8531d1c4e6768e94",
  "mcp-server/src/scac-mutation-registry.v6.generated.js": "1e190fb0ffc024b2a7c379aae966d55e74150a6d94a062d24abde19fb60e92bf",
  "migrations/0464_siep16_integrated_mutation_registry.sql": "d85f267542b9ce3c269ac3ce3989043dfd7da4f3d5674196aed96b7f8db4523e",
  "mcp-server/src/scac-mutation-registry.v7.generated.js": "83f19c1a998245f0465958a468f96459a6562e3353cdf0650457b645a470e4a1",
  "migrations/0466_siep17_forward_mutation_registry.sql": "069d16e50ffe5bf28a757a946de63bae9fdde3f3fcbe6cef733ed724534b27d0",
  "mcp-server/src/scac-mutation-registry.v8.generated.js": "37fec5ba9528c9d91c7d4c30830c9b5eeb57569da3e7024b8d33726544692d51",
  "migrations/0468_siep18_forward_mutation_registry.sql": "d398a22491e759290c20f2435f1c83a3034071069cca839491ce132e97461a73",
  "mcp-server/src/scac-mutation-registry.v9.generated.js": "c85115b40f7bd52f533a875decc78f298030f50daef1f12f772a306142c78ae0",
  "migrations/0471_source_merge_catalog_registry_successor.sql": "7d93dfb007a27e2e6798d1ff12cd7f57763745d82e564e49b056442ac47cc4d1",
  "mcp-server/src/scac-mutation-registry.v10.generated.js": "471c8609889a2fd4f55a413f4b2fca48ffdffbfdadb6cf6b3e72c2c5d8e7d8c2",
  "migrations/0481_codex_continuity_registry_activation.sql": "7a63ba4d86cdc15b25005c5874011a1441325c28235c2ccf8785ebfaba1bc8f3",
  "mcp-server/src/scac-mutation-registry.v11.generated.js": "19478e6a1b7f548dc55ee15bf0166826ad1eb29aa3f81199ee79d60290f9bad3",
  "migrations/0486_claude_continuity_registry_activation.sql": "270f817ef74fa87bbfaa4630fc26ce313f5684a7613975c80a64cf4d25ffb127",
  "mcp-server/src/scac-mutation-registry.v12.generated.js": "6f924c4b4df73ec8505c81db8824555a930dab2e047503d0f15f35f56f8bb2be",
  "migrations/0487_claude_startup_registry_activation.sql": "04fe724c10278534638562575fda16bc5b9dc963c1478e063ba13fbf9db620aa",
  "mcp-server/src/scac-mutation-registry.v13.generated.js": "c6abfd1cc8a89778ae938ac819e3fcdf8fb9f158129a9f32e7a34188b6c64bdc",
  "migrations/0488_claude_actor_hydration_registry_activation.sql": "2f170e330ab4582485e9074bbb69fdbbaeb4f2a635d6e0326f440ef1cfb8c948",
  "mcp-server/src/scac-mutation-registry.v14.generated.js": "a5032bb27133c1acc2feb214c2c701c5931ffa30a432d23cc6b3f6505507a5b9",
  "migrations/0489_claude_config_preservation_registry_activation.sql": "838be13404202e1a2077c8b52cc48a27572179f2814a3fe9c7f48e56b280cbec",
  "mcp-server/src/scac-mutation-registry.v15.generated.js": "6fecd62c9407ad47ae11c8ecd1c2f0d7f9cfedffc27c9500f23bc5c9ebfeee1b",
  "migrations/0490_codex_compaction_checkpoint_registry_activation.sql": "febd1bd3b6170767874636e34770dea84f814a24f170c39b955a7ec4465545a1",
  "mcp-server/src/scac-mutation-registry.v16.generated.js": "2bdcf517c9e2c418a20a75e742805686f1b6c83afbbc1fa070af0a2874018315",
  "migrations/0491_backup_guard_status_registry_activation.sql": "49129915fe40f41400c5fc769f82633b2da68949a29d193329fba2c6016e3913",
  "mcp-server/src/scac-mutation-registry.v17.generated.js": "5a1945eea59704fe7f1200937215be9f4fba6fda3a65d5a4321df9245873432d",
  "migrations/0492_sourced_shape_forward_correction_and_scac_successor.sql": "3c38ac9b0b22984603f58838aabcf97094e451ad166bc8526b09273f3f9755c6",
  "mcp-server/src/scac-mutation-registry.v18.generated.js": "980da606f08812d7f256427ca2f64a0209b8652cef834b4cf69de7a6f2afc59f",
  "migrations/0493_incident_work_request_link_scac_successor.sql": "0c7ba65bde7d0479cfec28c89d01f2721940c6be23a7ec9ad6c2f27e4f39ef3f",
  "mcp-server/src/scac-mutation-registry.v19.generated.js": "cd5c11d4caa792533ca46bdaf7ac1e3b698d633ea80c470a90c50192a1119dc9",
  "migrations/0494_codex_continuity_archive_registry.sql": "510e96efbff3870d87c4efefd6ad5bb1b32c7647cb3f5d306aa2aaead12a4a8e",
  "mcp-server/src/scac-mutation-registry.v20.generated.js": "dd679c9fa87fb45afe25d8508be462acfa395c532bf235f7fbdc0511b8678371",
  "migrations/0495_r06_hooks_correctness_scac_successor.sql": "97ba2964737373f31d17c089046ddae2337050dcdc278e309cd1a4797e16ad83",
  "mcp-server/src/scac-mutation-registry.v21.generated.js": "ff1088b58871db05d0eeb37e520eaefda35c1756b6b8144f9cd9d595b8e49b61",
  "migrations/0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql": "b8de4ce8bfa23c5ac06c4a1729456da4cfc301ec6b82e54b336ab072c3d6dca7",
  "mcp-server/src/scac-mutation-registry.v22.generated.js": "58e37870d1aba7750b841468ef0c4bea76cb75f18ee2a978eb4b3ce567302c20",
});
// WR-000068 rebases four Production-applied consumers of the sourced shape
// columns on the effective receipt-backed lineage. The v18 generator reads the
// exact witness definitions from these migrations, verifies their bytes, and
// rewrites only the named guard predicates; a drifted witness halts rendering.
export const SOURCED_SHAPE_FORWARD_CORRECTION_WITNESS_SHA256 = Object.freeze({
  "migrations/0306_sourced_work_shape_disposition.sql": "9a205a2c8c2ca50b61d5ee60b8883c0ff66a8138691d822faaf7ce4295ffb7af",
  "migrations/0333_shape_preserving_outcome_guards.sql": "431e77ebd8889172bb29493d7ec2833d0d8b3f7e64b9ba591cbec29742d624b5",
  "migrations/0426_withdraw_a_work_request_captured_in_error.sql": "151eddaae36b60fd1a6f0ad43f9577c03381ebd11b17b9a9741269d93bd2d395",
  "migrations/0470_source_merge_authority_projection.sql": "979c1312a6c6d41807c97a4893abe3bc6dc6716f21d83e5969be7f1372130967",
});
// 0467 is the reviewed SIEP-18 monitor source consumed by the v9 generator.
// It is not historical yet, but its bytes must still be exact: otherwise a
// mutable monitor function could silently change the generated 0468 artifact.
export const SIEP18_MONITOR_ARTIFACT_SHA256 =
  "96d9972351de67295c683096b9780243da6efe35db18b4baa80cfbe913969a9f";
export const DIRECT_REGISTRY_MIGRATION_ARTIFACT_SHA256 = Object.freeze({
  "migrations/0460_siep15_device_enrollment.sql": "76b3684c1f3ca0ce8622b64f3f6645a7cfb34b3a6cec1eeb7f6723884518bfc7",
  "migrations/0465_siep17_token_challenge_authority.sql": "4000706bc923738b6365e476a0a035093f19135ce8d76b6a22ca0b339764aef7",
  "migrations/0467_siep18_atomic_db_monitor_grants.sql": "96d9972351de67295c683096b9780243da6efe35db18b4baa80cfbe913969a9f",
  "migrations/0470_source_merge_authority_projection.sql": "979c1312a6c6d41807c97a4893abe3bc6dc6716f21d83e5969be7f1372130967",
});
export const STATIC_FRONTIER_MIGRATION_ARTIFACT_SHA256 = Object.freeze({
  "migrations/0456_siep13_artifact_registry.sql": "246b170ce9d7ef021b51fba34de391ff7ed4c7c4fd60c03ecd0504f7d220be7e",
  "migrations/0458_siep14_root_trust.sql": "9d76f539908d5d20c7a443f6f51641faafa090024111cf18316a4d3cf0abfe5a",
  "migrations/0463_retired_rule_delivery_cleanup.sql": "707c41c049174fdde4dfcf488ea100e96aabcb1e7b17a6c554c7bed336c316e5",
  "migrations/0469_siep18_exact_effects_trusted_principal.sql": "ab92662d77576f40c21330ac52aa145d861c288c5f64fc71ff52ad1212398ef1",
});
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
  role_authority: { count: 12, digest: "sha256:eb650de73032466b46787f4a5826b60b100591657489a7990d9161e2d6588648" },
});
// SIEP-13's forward-only successor includes the v3 typed registry lookup.
// The digest is replaced only from disposable-DB readback when 0341 changes.
export const SIEP13_DB_CATALOG_BASELINE = Object.freeze({
  projection_version: "scac-db-catalog-projection.v3",
  secdef_execute: { count: 298, digest: "sha256:d8306fc5bb4bd3348fe5197f0946c251c4f0486ecdf98914ba11a7af6e4682c5" },
  relation_dml: { count: 285, digest: "sha256:53d12ebf83db4661b0e55eb81f91ab510c34828424a2b945b66c0286134b0b0b" },
  column_dml: { count: 12, digest: "sha256:607e31d990653776243350d001ca465234e321349b05259751f8231ae3c2c44f" },
  role_authority: { count: 12, digest: "sha256:eb650de73032466b46787f4a5826b60b100591657489a7990d9161e2d6588648" },
});
// SIEP-14's successor includes the read-only v4 registry projection. The
// category digest is replaced only from disposable-DB readback.
export const SIEP14_DB_CATALOG_BASELINE = Object.freeze({
  projection_version: "scac-db-catalog-projection.v4",
  secdef_execute: { count: 302, digest: "sha256:abe2fb9009736fa4ae47270fcf5d2153bbf3e7a8ed01de861cced9a1bb2fee82" },
  relation_dml: { count: 285, digest: "sha256:53d12ebf83db4661b0e55eb81f91ab510c34828424a2b945b66c0286134b0b0b" },
  column_dml: { count: 12, digest: "sha256:607e31d990653776243350d001ca465234e321349b05259751f8231ae3c2c44f" },
  role_authority: { count: 12, digest: "sha256:eb650de73032466b46787f4a5826b60b100591657489a7990d9161e2d6588648" },
});
// SIEP-15 adds the safe device-status projection and the v5 registry lookup.
// The secdef digest starts fail-closed and is replaced only by disposable-DB readback.
export const SIEP15_DB_CATALOG_BASELINE = Object.freeze({
  projection_version: "scac-db-catalog-projection.v5",
  secdef_execute: { count: 307, digest: "sha256:d4671fbc10a54406acece348dbeaef08b044b8231037bf4ecce8e1d85cd2d24d" },
  relation_dml: { count: 285, digest: "sha256:53d12ebf83db4661b0e55eb81f91ab510c34828424a2b945b66c0286134b0b0b" },
  column_dml: { count: 12, digest: "sha256:607e31d990653776243350d001ca465234e321349b05259751f8231ae3c2c44f" },
  role_authority: { count: 12, digest: "sha256:eb650de73032466b46787f4a5826b60b100591657489a7990d9161e2d6588648" },
});
// SIEP-16 itself is source-only. This forward successor records the measured
// post-main database catalog and the current source inventory without
// rewriting the now-historical v5 artifacts.
export const SIEP16_DB_CATALOG_BASELINE = Object.freeze({
  projection_version: "scac-db-catalog-projection.v6",
  secdef_execute: { count: 311, digest: "sha256:78771a23004af05b61ea9491ed70248ee8cbbe77aa6ace8c6f0437983624470b" },
  relation_dml: { count: 285, digest: "sha256:53d12ebf83db4661b0e55eb81f91ab510c34828424a2b945b66c0286134b0b0b" },
  column_dml: { count: 12, digest: "sha256:607e31d990653776243350d001ca465234e321349b05259751f8231ae3c2c44f" },
  role_authority: { count: 12, digest: "sha256:eb650de73032466b46787f4a5826b60b100591657489a7990d9161e2d6588648" },
});
// Upstream source integration after the v6 seal adds source entrances. The v7
// successor also adds five read-only registry projection functions; the
// database digest is replaced only from disposable-DB readback.
export const SIEP16_INTEGRATED_DB_CATALOG_BASELINE = Object.freeze({
  projection_version: "scac-db-catalog-projection.v7",
  secdef_execute: { count: 315, digest: "sha256:d47181d79ffb352fdf2c707a6fa265f093a8c7edde76df4358a6805c89651022" },
  relation_dml: { count: 285, digest: "sha256:53d12ebf83db4661b0e55eb81f91ab510c34828424a2b945b66c0286134b0b0b" },
  column_dml: { count: 12, digest: "sha256:607e31d990653776243350d001ca465234e321349b05259751f8231ae3c2c44f" },
  role_authority: { count: 12, digest: "sha256:eb650de73032466b46787f4a5826b60b100591657489a7990d9161e2d6588648" },
});
// SIEP-17's forward successor includes the token/challenge authority source
// surface. Values are measured from disposable-DB readback only.
export const SIEP17_FORWARD_DB_CATALOG_BASELINE = Object.freeze({
  projection_version: "scac-db-catalog-projection.v8",
  secdef_execute: { count: 328, digest: "sha256:7c6a54dda8f8c4c6f4fcb6004f2544ba8231f212e1b2d8e5c9cc39651a2a216c" },
  relation_dml: { count: 285, digest: "sha256:53d12ebf83db4661b0e55eb81f91ab510c34828424a2b945b66c0286134b0b0b" },
  column_dml: { count: 12, digest: "sha256:607e31d990653776243350d001ca465234e321349b05259751f8231ae3c2c44f" },
  role_authority: { count: 12, digest: "sha256:eb650de73032466b46787f4a5826b60b100591657489a7990d9161e2d6588648" },
});
// The disposable-DB receipt before 0468 is applied. 0468 must first seal this
// exact predecessor catalog, then its four security-definer self-effects yield
// the v9 baseline below. Keeping both measurements makes the delta reviewable.
export const SIEP18_PRE_V9_DB_CATALOG_BASELINE = Object.freeze({
  projection_version: "scac-db-catalog-projection.v8-post-0467",
  secdef_execute: { count: 338, digest: "sha256:ccf023867a696884b2b9e50ae6eccc7b4e2afd9d7d6dbd1a93c01d8b1ec38555" },
  relation_dml: { count: 285, digest: "sha256:53d12ebf83db4661b0e55eb81f91ab510c34828424a2b945b66c0286134b0b0b" },
  column_dml: { count: 12, digest: "sha256:607e31d990653776243350d001ca465234e321349b05259751f8231ae3c2c44f" },
  role_authority: { count: 12, digest: "sha256:eb650de73032466b46787f4a5826b60b100591657489a7990d9161e2d6588648" },
});
// SIEP-18's forward successor binds the exact post-0468 catalog and the
// separately measured runtime DML grant snapshot. The grant snapshot is a
// derived monitor input and therefore does not add registry entry rows.
export const SIEP18_FORWARD_DB_CATALOG_BASELINE = Object.freeze({
  projection_version: "scac-db-catalog-projection.v9",
  secdef_execute: { count: 342, digest: "sha256:57444b408258e9ec0a0dd8d2b8062cc6f6575e0b97cd0e9faebbb7ca322e17af" },
  relation_dml: { count: 285, digest: "sha256:53d12ebf83db4661b0e55eb81f91ab510c34828424a2b945b66c0286134b0b0b" },
  column_dml: { count: 12, digest: "sha256:607e31d990653776243350d001ca465234e321349b05259751f8231ae3c2c44f" },
  role_authority: { count: 12, digest: "sha256:eb650de73032466b46787f4a5826b60b100591657489a7990d9161e2d6588648" },
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
  role_authority: { count: 12, digest: "sha256:eb650de73032466b46787f4a5826b60b100591657489a7990d9161e2d6588648" },
  runtime_dml_grants: { count: 297, digest: "sha256:0f04a50d8bc65e2dcc765b1981ab1d5091c809570f0a773db3f5c6e2b9d43501" },
});
// The successor's registration ACL changes the security-definer projection by
// four entries. Its digest is replaced only from disposable-DB readback.
export const SOURCE_MERGE_FORWARD_DB_CATALOG_BASELINE = Object.freeze({
  projection_version: "scac-db-catalog-projection.v10",
  secdef_execute: { count: 347, digest: "sha256:03624b669043c5e2e5a81633837f29ffb096b824a840456b26d7b6f3b405b467" },
  relation_dml: { count: 285, digest: "sha256:53d12ebf83db4661b0e55eb81f91ab510c34828424a2b945b66c0286134b0b0b" },
  column_dml: { count: 12, digest: "sha256:607e31d990653776243350d001ca465234e321349b05259751f8231ae3c2c44f" },
  role_authority: { count: 12, digest: "sha256:eb650de73032466b46787f4a5826b60b100591657489a7990d9161e2d6588648" },
  runtime_dml_grants: { count: 297, digest: "sha256:0f04a50d8bc65e2dcc765b1981ab1d5091c809570f0a773db3f5c6e2b9d43501" },
});
// Exact disposable-PG17 receipt after 0480_codex_continuity.sql. The runtime
// grant snapshot is separate from catalog entry rows but is bound by the
// reference monitor, so it must be measured with the ACL categories.
export const CODEX_CONTINUITY_PRE_V11_DB_CATALOG_BASELINE = Object.freeze({
  projection_version: "scac-db-catalog-projection.v11",
  secdef_execute: { count: 347, digest: "sha256:03624b669043c5e2e5a81633837f29ffb096b824a840456b26d7b6f3b405b467" },
  relation_dml: { count: 289, digest: "sha256:f9730debbe0301456fe10ea9eeafeff8f301b72d14e34d397bb6ad9cd1df71bd" },
  column_dml: { count: 12, digest: "sha256:607e31d990653776243350d001ca465234e321349b05259751f8231ae3c2c44f" },
  role_authority: { count: 12, digest: "sha256:eb650de73032466b46787f4a5826b60b100591657489a7990d9161e2d6588648" },
  runtime_dml_grants: { count: 301, digest: "sha256:dcf95363b3388bbb104e455a154fbe1da0a228f38df0c9317d8c191373706e73" },
});
// Filled from the rollback-only post-0480 receipt. The successor adds the v11
// lookup ACL to the security-definer census; all other measured categories are
// unchanged by the registry activation itself.
export const CODEX_CONTINUITY_FORWARD_DB_CATALOG_BASELINE = Object.freeze({
  ...CODEX_CONTINUITY_PRE_V11_DB_CATALOG_BASELINE,
  projection_version: "scac-db-catalog-projection.v11",
  secdef_execute: { count: 351, digest: "sha256:6bb739ea0422615f8150affcf24b83de0c2454ea485dc07d72f63bcda45a7014" },
});
// Replaced only from disposable-PG17 readback after 0485.
export const CLAUDE_CONTINUITY_PRE_V12_DB_CATALOG_BASELINE = Object.freeze({
  projection_version: "scac-db-catalog-projection.v12-pre",
  secdef_execute: { count: 351, digest: "sha256:6bb739ea0422615f8150affcf24b83de0c2454ea485dc07d72f63bcda45a7014" },
  relation_dml: { count: 295, digest: "sha256:a525431d9742a01545e1a63b6ef5c1ad64d783add25794782ee175c3e4c82904" },
  column_dml: { count: 12, digest: "sha256:607e31d990653776243350d001ca465234e321349b05259751f8231ae3c2c44f" },
  role_authority: { count: 12, digest: "sha256:eb650de73032466b46787f4a5826b60b100591657489a7990d9161e2d6588648" },
  runtime_dml_grants: { count: 307, digest: "sha256:5ac46a8d4226dae12c5a455be0080a472bf4e7f9dd3aa725004ec9c105be74a1" },
});
export const CLAUDE_CONTINUITY_FORWARD_DB_CATALOG_BASELINE = Object.freeze({
  ...CLAUDE_CONTINUITY_PRE_V12_DB_CATALOG_BASELINE,
  projection_version: "scac-db-catalog-projection.v12",
  secdef_execute: { count: 355, digest: "sha256:bb6d53a5fce3aee0b694303a346862423cb6a38efa80faf5decebb30aff3d783" },
});
// Exact post-0486 predecessor and rollback-only post-0487 successor receipts.
// The v13 activation adds only its four read-only registry projection grants.
export const CLAUDE_STARTUP_PRE_V13_DB_CATALOG_BASELINE =
  CLAUDE_CONTINUITY_FORWARD_DB_CATALOG_BASELINE;
export const CLAUDE_STARTUP_FORWARD_DB_CATALOG_BASELINE = Object.freeze({
  ...CLAUDE_STARTUP_PRE_V13_DB_CATALOG_BASELINE,
  projection_version: "scac-db-catalog-projection.v13",
  secdef_execute: { count: 359, digest: "sha256:586dc084ef9eb234a352f1c97c69692af498b9581d1e2bd770bbd1e89f09414e" },
});
export const CLAUDE_ACTOR_HYDRATION_PRE_V14_DB_CATALOG_BASELINE =
  CLAUDE_STARTUP_FORWARD_DB_CATALOG_BASELINE;
export const CLAUDE_ACTOR_HYDRATION_FORWARD_DB_CATALOG_BASELINE = Object.freeze({
  ...CLAUDE_ACTOR_HYDRATION_PRE_V14_DB_CATALOG_BASELINE,
  projection_version: "scac-db-catalog-projection.v14",
  secdef_execute: { count: 363, digest: "sha256:c3dcffa37314df9b44f68b20a0baac5555531d3d9cf136a91020196f88234a8a" },
});
export const CLAUDE_CONFIG_PRESERVATION_PRE_V15_DB_CATALOG_BASELINE =
  CLAUDE_ACTOR_HYDRATION_FORWARD_DB_CATALOG_BASELINE;
export const CLAUDE_CONFIG_PRESERVATION_FORWARD_DB_CATALOG_BASELINE = Object.freeze({
  ...CLAUDE_CONFIG_PRESERVATION_PRE_V15_DB_CATALOG_BASELINE,
  projection_version: "scac-db-catalog-projection.v15",
  secdef_execute: { count: 367, digest: "sha256:2fd333dec1d4ed6b33439e07f29fef53c86ce02a413a8121275a8e3ebc0e8064" },
});
export const CODEX_COMPACTION_CHECKPOINT_PRE_V16_DB_CATALOG_BASELINE =
  CLAUDE_CONFIG_PRESERVATION_FORWARD_DB_CATALOG_BASELINE;
export const CODEX_COMPACTION_CHECKPOINT_FORWARD_DB_CATALOG_BASELINE = Object.freeze({
  ...CODEX_COMPACTION_CHECKPOINT_PRE_V16_DB_CATALOG_BASELINE,
  projection_version: "scac-db-catalog-projection.v16",
  // Exact disposable-Postgres 17 readback after the generated 0490 successor.
  secdef_execute: { count: 371, digest: "sha256:0afe988d8320a159151cd8f4673c586983d8df3d91578ef344c00e7d57bc9413" },
});
export const BACKUP_GUARD_STATUS_PRE_V17_DB_CATALOG_BASELINE =
  CODEX_COMPACTION_CHECKPOINT_FORWARD_DB_CATALOG_BASELINE;
export const BACKUP_GUARD_STATUS_FORWARD_DB_CATALOG_BASELINE = Object.freeze({
  ...BACKUP_GUARD_STATUS_PRE_V17_DB_CATALOG_BASELINE,
  projection_version: "scac-db-catalog-projection.v17",
  // Four registration-function ACLs; verified against the complete local successor.
  secdef_execute: { count: 375, digest: "sha256:07e5d503bd30646b1a697d911cf9df3749eab5bc57c111dfe751f63a6fb20eb8" },
});
export const SOURCED_SHAPE_FORWARD_CORRECTION_PRE_V18_DB_CATALOG_BASELINE =
  BACKUP_GUARD_STATUS_FORWARD_DB_CATALOG_BASELINE;
export const SOURCED_SHAPE_FORWARD_CORRECTION_FORWARD_DB_CATALOG_BASELINE = Object.freeze({
  ...SOURCED_SHAPE_FORWARD_CORRECTION_PRE_V18_DB_CATALOG_BASELINE,
  projection_version: "scac-db-catalog-projection.v18",
  // Four v18 registration-function ACLs plus the two narrow lineage-projection
  // grants (carr_reader, carr_writer); read back from the complete disposable
  // 0492 successor, never from Production or a caller.
  secdef_execute: { count: 381, digest: "sha256:71595cc691e0c48d139a843f6aae5ddd5b72f428a91821741c1ac830e8a6ff75" },
});
export const INCIDENT_WORK_REQUEST_LINK_PRE_V19_DB_CATALOG_BASELINE =
  SOURCED_SHAPE_FORWARD_CORRECTION_FORWARD_DB_CATALOG_BASELINE;
export const INCIDENT_WORK_REQUEST_LINK_FORWARD_DB_CATALOG_BASELINE = Object.freeze({
  ...INCIDENT_WORK_REQUEST_LINK_PRE_V19_DB_CATALOG_BASELINE,
  projection_version: "scac-db-catalog-projection.v19",
  // Filled from the complete disposable-Postgres 0493 successor readback.
  secdef_execute: { count: 385, digest: "sha256:1ae04999d90aaebabeecb0564536b53e8a4b878ac128ffe2f6e25fe1a4dc0080" },
});
export const CONTINUITY_ARCHIVE_PRE_V20_DB_CATALOG_BASELINE =
  INCIDENT_WORK_REQUEST_LINK_FORWARD_DB_CATALOG_BASELINE;
export const CONTINUITY_ARCHIVE_FORWARD_DB_CATALOG_BASELINE = Object.freeze({
  ...CONTINUITY_ARCHIVE_PRE_V20_DB_CATALOG_BASELINE,
  projection_version: "scac-db-catalog-projection.v20",
  // Filled from the complete disposable-Postgres 0494 successor readback: the
  // four v19/v20 seal-and-catalog functions this registry-only successor adds
  // are the entire delta from the v19 receipt.
  secdef_execute: { count: 389, digest: "sha256:570bebcdfb87098b8b71ca59ed7e9a2fb5849099d6e3ba10cb99c934421aceb7" },
});
export const R06_HOOKS_CORRECTNESS_PRE_V21_DB_CATALOG_BASELINE =
  CONTINUITY_ARCHIVE_FORWARD_DB_CATALOG_BASELINE;
export const R06_HOOKS_CORRECTNESS_FORWARD_DB_CATALOG_BASELINE = Object.freeze({
  ...R06_HOOKS_CORRECTNESS_PRE_V21_DB_CATALOG_BASELINE,
  projection_version: "scac-db-catalog-projection.v21",
  // Filled from the complete disposable-Postgres 0495 successor readback: the
  // four v20/v21 seal-and-catalog functions this registry-only successor adds
  // are the entire delta from the v20 receipt.
  secdef_execute: { count: 393, digest: "sha256:6889d02e1c7e8eda58e8e91cdb61da685f307153696448832b27b8cf3a0e2bb7" },
});
export const DOCTORCRE_PORTFOLIO_PRE_V22_DB_CATALOG_BASELINE =
  R06_HOOKS_CORRECTNESS_FORWARD_DB_CATALOG_BASELINE;
export const DOCTORCRE_PORTFOLIO_FORWARD_DB_CATALOG_BASELINE = Object.freeze({
  ...R06_HOOKS_CORRECTNESS_FORWARD_DB_CATALOG_BASELINE,
  projection_version: "scac-db-catalog-projection.v22",
  // Read back from a clean disposable Postgres carrying db/schema.sql and every
  // migration through this one. The security-definer surface is the entire
  // delta from v21's 393: the portfolio's canonical, digest, structure,
  // governance and definer-write functions, PLUS the four seal and catalog
  // functions this successor itself installs -- which is why the figure is
  // taken after the whole migration runs and not after its domain SQL alone.
  // relation_dml is unchanged because the portfolio tables grant SELECT only,
  // every write going through a definer function, and role_authority is
  // unchanged because this change creates no role.
  secdef_execute: { count: 450, digest: "sha256:dbd281eef92b9232e4bdd9bfb35c5884011b9d8f7177ebae919557a4aa25047b" },
});

export const R07_REPO_HYGIENE_JANITOR_PRE_V23_DB_CATALOG_BASELINE =
  DOCTORCRE_PORTFOLIO_FORWARD_DB_CATALOG_BASELINE;
export const R07_REPO_HYGIENE_JANITOR_FORWARD_DB_CATALOG_BASELINE = Object.freeze({
  ...DOCTORCRE_PORTFOLIO_FORWARD_DB_CATALOG_BASELINE,
  projection_version: "scac-db-catalog-projection.v23",
  // Read back from a clean disposable Postgres carrying db/schema.sql and every
  // migration through this one. This successor is registry-only: the repo
  // hygiene janitor is a tools/ script and an uninstalled LaunchAgent
  // definition, so it creates no table, no role and no domain function. The
  // entire security-definer delta from v22's 450 is the four seal-and-catalog
  // functions this successor installs for itself, exactly as the v20 and v21
  // registry-only successors before it. relation_dml, column_dml,
  // role_authority and runtime_dml_grants are unchanged for the same reason.
  secdef_execute: { count: 454, digest: "sha256:d4a15c2d2f507d75c4c3c3d8c722d4d09f704fb141c8783ea0a640a721a0af55" },
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

function directMigrationPreimage(path) {
  if (DIRECT_MIGRATION_PREIMAGES.schema_version !==
      "scac-direct-registry-migration-preimages.v1")
    throw new Error("unsupported direct-migration preimage schema");
  if (DIRECT_MIGRATION_PREIMAGES.source_commit !==
      "f422e1720f33c8f7c24cd7433f151b115ef37ee7")
    throw new Error("unexpected direct-migration preimage provenance");
  const fixturePaths = Object.keys(DIRECT_MIGRATION_PREIMAGES.artifacts).sort();
  const expectedPaths = Object.keys(DIRECT_REGISTRY_MIGRATION_ARTIFACT_SHA256).sort();
  if (JSON.stringify(fixturePaths) !== JSON.stringify(expectedPaths))
    throw new Error("direct-migration preimage path set is incomplete");
  const fixture = DIRECT_MIGRATION_PREIMAGES.artifacts[path];
  if (!fixture || typeof fixture.preimage !== "string" ||
      typeof fixture.owner_exclusion !== "boolean")
    throw new Error(`${path} direct-migration preimage is malformed`);
  if (sha256(fixture.preimage) !== fixture.preimage_sha256)
    throw new Error(`${path} direct-migration preimage SHA drifted`);
  if (fixture.expected_output_sha256 !==
      DIRECT_REGISTRY_MIGRATION_ARTIFACT_SHA256[path])
    throw new Error(`${path} direct-migration output SHA pin drifted`);
  return fixture;
}

function staticFrontierMigration(path) {
  if (STATIC_FRONTIER_PREIMAGES.schema_version !==
      "scac-static-frontier-migration-preimages.v1")
    throw new Error("unsupported static-frontier preimage schema");
  if (STATIC_FRONTIER_PREIMAGES.source_commit !==
      "f422e1720f33c8f7c24cd7433f151b115ef37ee7")
    throw new Error("unexpected static-frontier preimage provenance");
  const fixturePaths = Object.keys(STATIC_FRONTIER_PREIMAGES.artifacts).sort();
  const expectedPaths = Object.keys(STATIC_FRONTIER_MIGRATION_ARTIFACT_SHA256).sort();
  if (JSON.stringify(fixturePaths) !== JSON.stringify(expectedPaths))
    throw new Error("static-frontier preimage path set is incomplete");
  const fixture = STATIC_FRONTIER_PREIMAGES.artifacts[path];
  if (!fixture || typeof fixture.preimage !== "string")
    throw new Error(`${path} static-frontier preimage is malformed`);
  const digest = sha256(fixture.preimage);
  if (digest !== fixture.preimage_sha256 || digest !== fixture.expected_output_sha256 ||
      digest !== STATIC_FRONTIER_MIGRATION_ARTIFACT_SHA256[path])
    throw new Error(`${path} static-frontier preimage SHA drifted`);
  return fixture.preimage;
}

function fullEntrySetSealsFromLocalDatabase(dsn) {
  let parsed;
  try {
    parsed = new URL(dsn);
  } catch {
    throw new Error("full-entry-set seal blessing requires a PostgreSQL URL");
  }
  if (!["postgres:", "postgresql:"].includes(parsed.protocol) ||
      !["127.0.0.1", "localhost"].includes(parsed.hostname))
    throw new Error("full-entry-set seals may be blessed only from a local disposable database");
  const sql = `
select v.registry_version,
       v.entry_set_digest,
       'sha256:'||encode(public.digest(convert_to(
         coalesce(string_agg(e.entry_digest,',' order by e.ingress_key collate "C"),''),
         'UTF8'),'sha256'),'hex') recomputed
  from ops.scac_mutation_registry_version v
  left join ops.scac_mutation_registry_entry e using(registry_version)
 group by v.registry_version,v.entry_set_digest
 order by split_part(v.registry_version,'.v',2)::integer`;
  const output = execFileSync("psql", [dsn, "-X", "-A", "-t", "-F", "\t",
    "-v", "ON_ERROR_STOP=1", "-c", sql], { encoding: "utf8" });
  const rows = output.trim().split("\n").filter(Boolean).map(line => line.split("\t"));
  const expectedVersions = Array.from({ length: 10 }, (_, index) =>
    `scac-mutation-registry.v${index + 1}`);
  if (rows.length !== expectedVersions.length ||
      rows.some((row, index) => row.length !== 3 || row[0] !== expectedVersions[index] ||
        row[1] !== row[2] || !/^sha256:[0-9a-f]{64}$/.test(row[2])))
    throw new Error("local database registry entry sets are incomplete, stale, or malformed");
  return Object.fromEntries(rows.map(([version, _stored, recomputed]) => [version, recomputed]));
}

export function replaceExactlyOnce(value, search, replacement, label) {
  const first = value.indexOf(search);
  const second = first < 0 ? -1 : value.indexOf(search, first + search.length);
  if (first < 0 || second >= 0)
    throw new Error(`${label} marker count must be exactly one`);
  return `${value.slice(0, first)}${replacement}${value.slice(first + search.length)}`;
}

function replaceLegacyOrVerifyCurrent(value, legacy, current, label, expectedCount = 1) {
  const legacyCount = value.split(legacy).length - 1;
  const currentCount = value.split(current).length - 1;
  if (legacyCount === expectedCount && currentCount === 0) return value.replaceAll(legacy, current);
  if (legacyCount === 0 && currentCount === expectedCount) return value;
  throw new Error(`${label} must contain exactly ${expectedCount} legacy or current marker(s)`);
}

const LEGACY_ROLE_CENSUS_SEED =
  "    select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union\n" +
  "    select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci'";
const PORTABLE_ROLE_CENSUS_SEED =
  "    select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' and not rolcanlogin and not rolsuper union\n" +
  "    select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname~'^carr_' and other.rolname<>'carr_ci' and not other.rolcanlogin and not other.rolsuper";
const LEGACY_ROLE_CENSUS_RETURN =
  "  return observed_count=95 and observed_digest='sha256:082b8570b428c33296c801871177f6bfb34e9c070513d4b1db23007f4edecafb';";
const PORTABLE_ROLE_CENSUS_RETURN =
  "  if exists (select 1 from pg_auth_members m join pg_roles g on g.oid=m.roleid join pg_roles mem on mem.oid=m.member where mem.rolname~'^carr_' and (g.rolsuper or g.rolname~'^(neon_|pg_)')) then return false; end if;\n" +
  "  return observed_count=12 and observed_digest='sha256:eb650de73032466b46787f4a5826b60b100591657489a7990d9161e2d6588648';";

export function renderDirectRegistryRedefinition(source, { ownerExclusion = false } = {}) {
  let rendered = replaceLegacyOrVerifyCurrent(source, LEGACY_ROLE_CENSUS_SEED,
    PORTABLE_ROLE_CENSUS_SEED, "direct migration role census seed");
  rendered = replaceLegacyOrVerifyCurrent(rendered, LEGACY_ROLE_CENSUS_RETURN,
    PORTABLE_ROLE_CENSUS_RETURN, "direct migration role census return");
  if (!ownerExclusion) return rendered;
  const ownerExclusionReplacements = [
    ["where c.relkind in ('r','p') and (a.grantee=0 or a.grantee in(select oid from connected))",
      "where c.relkind in ('r','p') and a.grantee<>c.relowner\n      and (a.grantee=0 or a.grantee in(select oid from connected))"],
    ["where c.relkind in ('r','p') and att.attnum>0 and not att.attisdropped\n      and (a.grantee=0 or a.grantee in(select oid from connected))",
      "where c.relkind in ('r','p') and att.attnum>0 and not att.attisdropped\n      and a.grantee<>c.relowner\n      and (a.grantee=0 or a.grantee in(select oid from connected))"],
    ["c.relkind in ('v','m','f') and\n      (a.grantee=0 or a.grantee in(select oid from runtime_roles))",
      "c.relkind in ('v','m','f') and a.grantee<>c.relowner and\n      (a.grantee=0 or a.grantee in(select oid from runtime_roles))"],
    ["c.relkind in ('v','m','f') and att.attnum>0 and not att.attisdropped and\n      (a.grantee=0 or a.grantee in(select oid from runtime_roles))",
      "c.relkind in ('v','m','f') and att.attnum>0 and not att.attisdropped and\n      a.grantee<>c.relowner and\n      (a.grantee=0 or a.grantee in(select oid from runtime_roles))"],
    ["where c.relkind in ('r','p') and\n        (a.grantee=0 or a.grantee in(select oid from runtime_roles))",
      "where c.relkind in ('r','p') and a.grantee<>c.relowner and\n        (a.grantee=0 or a.grantee in(select oid from runtime_roles))", 2],
    ["where c.relkind in ('r','p') and att.attnum>0 and not att.attisdropped\n        and (a.grantee=0 or a.grantee in(select oid from runtime_roles))",
      "where c.relkind in ('r','p') and att.attnum>0 and not att.attisdropped\n        and a.grantee<>c.relowner\n        and (a.grantee=0 or a.grantee in(select oid from runtime_roles))"],
    ["cross join lateral aclexplode(att.attacl) a where c.relkind in ('r','p')\n        and att.attnum>0 and not att.attisdropped and\n        (a.grantee=0 or a.grantee in(select oid from runtime_roles))",
      "cross join lateral aclexplode(att.attacl) a where c.relkind in ('r','p')\n        and att.attnum>0 and not att.attisdropped and a.grantee<>c.relowner and\n        (a.grantee=0 or a.grantee in(select oid from runtime_roles))"],
  ];
  for (const [legacy, current, expectedCount] of ownerExclusionReplacements)
    rendered = replaceLegacyOrVerifyCurrent(rendered, legacy, current,
      "0467 owner-exclusion projection", expectedCount);
  return rendered;
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

export function mcpInventory(tools = defaultTools) {
  return Object.entries(requireTools(tools)).sort(([left], [right]) => left.localeCompare(right)).map(([name, tool]) => {
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

// A LaunchAgent with no trigger and no load-time start cannot run: launchd has
// no moment at which to fire it. Such a definition is a reviewed artifact rather
// than a deployed service, so it is exempt from ops.service catalog closure --
// requiring a deploy_mechanism for it would assert a deployment that must not
// exist. The exemption is derived from the artifact itself rather than from a
// list, so it cannot drift out of step with the file it describes, and it is
// two-directional: a definition-only agent that DOES appear in the service
// catalog is a contradiction and still refuses below.
const LAUNCHD_TRIGGER_KEYS = Object.freeze([
  "StartCalendarInterval", "StartInterval", "WatchPaths", "KeepAlive",
  "StartOnMount", "QueueDirectories", "Sockets", "MachServices",
]);

export function isDefinitionOnlyLaunchd(plist) {
  if (!plist || typeof plist !== "object") return false;
  if (plist.RunAtLoad === true) return false;
  return !LAUNCHD_TRIGGER_KEYS.some(key => plist[key] !== undefined);
}

export function validateLaunchdAuthorityCatalogs(launchdPaths, services, legacy,
  definitionOnlyPaths = []) {
  const definitionOnly = new Set(definitionOnlyPaths);
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
  for (const path of definitionOnly) {
    if (!reviewedPaths.has(path))
      throw new Error(`definition-only launchd path is not a reviewed source: ${path}`);
    if (servicesByPlist.has(path))
      throw new Error(`definition-only launchd agent claims a deploy mechanism: ${path}`);
  }
  const missingServices = [...reviewedPaths]
    .filter(path => !servicesByPlist.has(path) && !definitionOnly.has(path)).sort();
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
  const definitionOnlyPaths = launchdPaths.filter(path =>
    isDefinitionOnlyLaunchd(parsePlistXml(readFileSync(resolve(REPO_ROOT, path), "utf8"))));
  const { servicesByPlist, legacyByPlist } = validateLaunchdAuthorityCatalogs(
    launchdPaths, services, legacy, definitionOnlyPaths);
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
    const definitionOnly = isDefinitionOnlyLaunchd(plist);
    // A deployed agent must name the service environment that deploys it. A
    // definition-only agent has no deployment to name, and is inventoried with
    // an explicit non-deployed authority ref instead of an absent one, so it is
    // still a registered row a reviewer can see rather than a silent gap.
    if (!serviceMappings.length && !definitionOnly)
      throw new Error(`launchd workflow lacks ops.service authority mapping: ${source_locator}`);
    // A DISTINCT NAMESPACE, not an ops.service_environment ref: this agent has
    // no service environment, and borrowing that prefix would have made it
    // count as one wherever deployed environments are totalled.
    const physicalAuthorityRefs = definitionOnly
      ? ["ops.definition_only_launchd:not_deployed"]
      : serviceMappings.map(mapping =>
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

export function fullInventory(tools = defaultTools) {
  requireTools(tools);
  return [...mcpInventory(tools), ...nonMcpInventory(), ...jobDefinitionInventory(), ...workflowDefinitionInventory()]
    .sort((left, right) => left.ingress_key.localeCompare(right.ingress_key));
}

const SOURCE_INVENTORY_VERSION_KEYS = Object.freeze({
  [REGISTRY_V2_VERSION]: "v2",
  [REGISTRY_V3_VERSION]: "v3",
  [REGISTRY_V4_VERSION]: "v4",
  [REGISTRY_V5_VERSION]: "v5",
  [REGISTRY_V6_VERSION]: "v6",
  [REGISTRY_V7_VERSION]: "v7",
  [REGISTRY_V8_VERSION]: "v8",
  [REGISTRY_V9_VERSION]: "v9",
  [REGISTRY_V10_VERSION]: "v10",
  [REGISTRY_V11_VERSION]: "v11",
  [REGISTRY_V12_VERSION]: "v12",
  [REGISTRY_V13_VERSION]: "v13",
  [REGISTRY_V14_VERSION]: "v14",
  [REGISTRY_V15_VERSION]: "v15",
  [REGISTRY_V16_VERSION]: "v16",
  [REGISTRY_V17_VERSION]: "v17",
  [REGISTRY_V18_VERSION]: "v18",
  [REGISTRY_V19_VERSION]: "v19",
  [REGISTRY_V20_VERSION]: "v20",
  [REGISTRY_V21_VERSION]: "v21",
  [REGISTRY_V22_VERSION]: "v22",
  [REGISTRY_V23_VERSION]: "v23",
});

function sourceInventoryFixtureDigest(rows) {
  return createHash("sha256").update(JSON.stringify(rows)).digest("hex");
}

export function frozenInventory(version) {
  const targetKey = SOURCE_INVENTORY_VERSION_KEYS[version];
  if (!targetKey)
    throw new Error(`no frozen source-inventory fixture for ${version}`);
  const fixture = SOURCE_INVENTORY_FIXTURES;
  if (fixture.schema_version !== "scac-registry-source-inventory-fixtures.v1")
    throw new Error(`unsupported source-inventory fixture schema: ${fixture.schema_version}`);
  if (fixture.source_commit !== "f422e1720f33c8f7c24cd7433f151b115ef37ee7")
    throw new Error(`unexpected source-inventory fixture provenance: ${fixture.source_commit}`);
  const rowsByKey = new Map(fixture.base.rows.map(row => [row.ingress_key, row]));
  let expectedCount = fixture.base.expected_count;
  let expectedDigest = fixture.base.expected_sha256;
  if (!fixture.base.versions.includes(targetKey)) {
    let found = false;
    for (const patch of fixture.patches) {
      for (const ingressKey of patch.remove) rowsByKey.delete(ingressKey);
      for (const row of patch.upsert) rowsByKey.set(row.ingress_key, row);
      for (const [ingressKey, replacement] of Object.entries(patch.row_replacements || {})) {
        const current = rowsByKey.get(ingressKey);
        if (!current || !replacement || typeof replacement !== "object" || Array.isArray(replacement))
          throw new Error(`${patch.version} row replacement is malformed: ${ingressKey}`);
        rowsByKey.set(ingressKey, { ...current, ...replacement });
      }
      for (const [sourceLocator, sourceDigest] of Object.entries(patch.source_digest_replacements || {})) {
        if (!/^[0-9a-f]{64}$/.test(sourceDigest))
          throw new Error(`${patch.version} source digest replacement is malformed: ${sourceLocator}`);
        for (const [ingressKey, row] of rowsByKey) {
          if (row.source_locator === sourceLocator)
            rowsByKey.set(ingressKey, { ...row, source_digest: sourceDigest });
        }
      }
      expectedCount = patch.expected_count;
      expectedDigest = patch.expected_sha256;
      if (patch.version === targetKey) {
        found = true;
        break;
      }
    }
    if (!found) throw new Error(`source-inventory fixture patch missing for ${targetKey}`);
  }
  const rows = [...rowsByKey.values()]
    .sort((left, right) => left.ingress_key.localeCompare(right.ingress_key));
  const observedDigest = sourceInventoryFixtureDigest(rows);
  if (rows.length !== expectedCount || observedDigest !== expectedDigest)
    throw new Error(`${targetKey} source-inventory fixture drifted: count ${rows.length}/${expectedCount}, sha256 ${observedDigest}/${expectedDigest}`);
  return Object.freeze(rows.map(row => Object.freeze(structuredClone(row))));
}

export function assertCurrentSourceInventoryMatchesFixture(tools = defaultTools,
  version = REGISTRY_V23_VERSION) {
  const current = fullInventory(tools);
  let frozen = frozenInventory(version);
  const review = SOURCE_INVENTORY_FIXTURES.current_source_review;
  if (review) {
    const expectedBase = version.split(".").at(-1);
    if (review.base_version !== expectedBase || !Array.isArray(review.upsert) ||
        !Number.isInteger(review.expected_count) ||
        !/^[0-9a-f]{64}$/.test(review.expected_sha256 || "") ||
        typeof review.reason !== "string" || !review.reason.trim())
      throw new Error("current source-inventory review is malformed or bound to the wrong frontier");
    const reviewedByKey = new Map(frozen.map(row => [row.ingress_key, row]));
    for (const row of review.upsert) {
      if (!row || typeof row.ingress_key !== "string" || !reviewedByKey.has(row.ingress_key))
        throw new Error(`current source-inventory review has unknown ingress ${row?.ingress_key}`);
      reviewedByKey.set(row.ingress_key, row);
    }
    const reviewed = [...reviewedByKey.values()]
      .sort((left, right) => left.ingress_key.localeCompare(right.ingress_key));
    const reviewedDigest = sourceInventoryFixtureDigest(reviewed);
    if (reviewed.length !== review.expected_count || reviewedDigest !== review.expected_sha256)
      throw new Error(`current source-inventory review drifted: count ${reviewed.length}/${review.expected_count}, sha256 ${reviewedDigest}/${review.expected_sha256}`);
    frozen = reviewed;
  }
  const currentDigest = sourceInventoryFixtureDigest(current);
  const frozenDigest = sourceInventoryFixtureDigest(frozen);
  if (current.length !== frozen.length || currentDigest !== frozenDigest)
    throw new Error(`current source inventory drifted from the ${version} frontier fixture: count ${current.length}/${frozen.length}, sha256 ${currentDigest}/${frozenDigest}`);
  return true;
}

export function registryDigestFor(version, rows = fullInventory(), dbCatalogBaseline = DB_CATALOG_BASELINE) {
  if (![REGISTRY_VERSION, REGISTRY_V2_VERSION, REGISTRY_V3_VERSION, REGISTRY_V4_VERSION,
    REGISTRY_V5_VERSION, REGISTRY_V6_VERSION, REGISTRY_V7_VERSION, REGISTRY_V8_VERSION,
    REGISTRY_V9_VERSION, REGISTRY_V10_VERSION, REGISTRY_V11_VERSION,
    REGISTRY_V12_VERSION, REGISTRY_V13_VERSION, REGISTRY_V14_VERSION,
    REGISTRY_V15_VERSION, REGISTRY_V16_VERSION, REGISTRY_V17_VERSION,
    REGISTRY_V18_VERSION, REGISTRY_V19_VERSION, REGISTRY_V20_VERSION,
    REGISTRY_V21_VERSION, REGISTRY_V22_VERSION,
    REGISTRY_V23_VERSION].includes(version))
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
  // WR-000048: v1 (migration 0454) is sealed/frozen, not part of this
  // regeneration cascade -- its predecessor reference must be the FROZEN
  // HISTORICAL_REGISTRY_SEALS.v1 (which matches 0454's actual committed
  // bytes exactly), never a fresh registrySeal() computed against the
  // CURRENT (larger, still-growing) fullInventory(). Using a fresh
  // computation here silently drifted from what 0454 actually contains as
  // the codebase's tool/rule inventory grew after 0454 was authored, and
  // would raise "sealed SCAC mutation registry v1 changed during successor
  // creation" the first time this generator was actually re-run end to end.
  const predecessor = HISTORICAL_REGISTRY_SEALS.v1;
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
`    select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' and not rolcanlogin and not rolsuper union\n` +
`    select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname~'^carr_' and other.rolname<>'carr_ci' and not other.rolcanlogin and not other.rolsuper\n` +
`  ), role_rows as (\n` +
`    select 'db-role:'||r.rolname ingress_key,jsonb_build_object('ingress_key','db-role:'||r.rolname,'row_kind','role','role',r.rolname,'login',r.rolcanlogin,'inherit',r.rolinherit,'superuser',r.rolsuper,'create_role',r.rolcreaterole,'create_db',r.rolcreatedb,'replication',r.rolreplication,'bypass_rls',r.rolbypassrls) row from pg_roles r where r.oid in(select oid from connected)\n` +
`  ), membership_rows as (\n` +
`    select 'db-role-membership:'||role.rolname||':'||member.rolname ingress_key,jsonb_build_object('ingress_key','db-role-membership:'||role.rolname||':'||member.rolname,'row_kind','membership','role',role.rolname,'member',member.rolname,'admin_option',m.admin_option,'inherit_option',m.inherit_option,'set_option',m.set_option) row from pg_auth_members m join pg_roles role on role.oid=m.roleid join pg_roles member on member.oid=m.member where m.roleid in(select oid from connected) and m.member in(select oid from connected)\n` +
`  ), ownership_rows as (\n` +
`    select 'db-function-owner:'||n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||'):'||owner.rolname ingress_key,jsonb_build_object('ingress_key','db-function-owner:'||n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||'):'||owner.rolname,'row_kind','function_owner','signature',n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||')','owner',owner.rolname) row from pg_proc p join pg_namespace n on n.oid=p.pronamespace join pg_roles owner on owner.oid=p.proowner where n.nspname not in ('pg_catalog','information_schema') and p.prokind in ('f','p') and owner.oid in(select oid from connected) and not owner.rolsuper and owner.rolname<>'neondb_owner' union all\n` +
`    select 'db-relation-owner:'||n.nspname||'.'||c.relname||':'||owner.rolname,jsonb_build_object('ingress_key','db-relation-owner:'||n.nspname||'.'||c.relname||':'||owner.rolname,'row_kind','relation_owner','relation',n.nspname||'.'||c.relname,'relation_kind',c.relkind,'owner',owner.rolname) row from pg_class c join pg_namespace n on n.oid=c.relnamespace join pg_roles owner on owner.oid=c.relowner where n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f') and owner.oid in(select oid from connected) and not owner.rolsuper and owner.rolname<>'neondb_owner'\n` +
`  ), observed as (select * from role_rows union all select * from membership_rows union all select * from ownership_rows)\n` +
`  select count(*),'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(jsonb_agg(row order by ingress_key collate "C", ops.scac_canonical_json(row) collate "C"),'[]'::jsonb)),'UTF8'),'sha256'),'hex') into observed_count,observed_digest from observed;\n` +
`  if exists (select 1 from pg_auth_members m join pg_roles g on g.oid=m.roleid join pg_roles mem on mem.oid=m.member where mem.rolname~'^carr_' and (g.rolsuper or g.rolname~'^(neon_|pg_)')) then return false; end if;\n` +
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
  dbCatalogBaseline = SIEP15_DB_CATALOG_BASELINE, predecessorSql = undefined) {
  const { v1: v1Seal, v2: v2Seal, v3: v3Seal, v4: v4Seal } = HISTORICAL_REGISTRY_SEALS;
  const v5Digest = registryDigestFor(REGISTRY_V5_VERSION, rows, dbCatalogBaseline);
  const v5CatalogCount = dbCatalogBaseline.secdef_execute.count +
    dbCatalogBaseline.relation_dml.count + dbCatalogBaseline.column_dml.count;
  const v5EntryCount = rows.length + v5CatalogCount;
  const v4Path = "migrations/0459_siep14_forward_mutation_registry.sql";
  const v4Sql = predecessorSql ?? renderSIEP14RegistrySql(
    frozenInventory(REGISTRY_V4_VERSION), SIEP14_DB_CATALOG_BASELINE);
  const observedV4Sha = sha256(v4Sql);
  if (observedV4Sha !== HISTORICAL_REGISTRY_ARTIFACT_SHA256[v4Path])
    throw new Error(`sealed historical SCAC v4 migration changed: ${observedV4Sha}`);
  let sql = v4Sql
    .replaceAll(JSON.stringify(SIEP14_DB_CATALOG_BASELINE), JSON.stringify(dbCatalogBaseline))
    .replaceAll(
      `if observed_count<>${SIEP14_DB_CATALOG_BASELINE.secdef_execute.count} or observed_digest<>'${SIEP14_DB_CATALOG_BASELINE.secdef_execute.digest}' then return false; end if;`,
      `if observed_count<>${dbCatalogBaseline.secdef_execute.count} or observed_digest<>'${dbCatalogBaseline.secdef_execute.digest}' then return false; end if;`,
    )
    .replaceAll(
      `return observed_count=${SIEP14_DB_CATALOG_BASELINE.role_authority.count} and observed_digest='${SIEP14_DB_CATALOG_BASELINE.role_authority.digest}';`,
      `return observed_count=${dbCatalogBaseline.role_authority.count} and observed_digest='${dbCatalogBaseline.role_authority.digest}';`,
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
  dbCatalogBaseline = SIEP16_DB_CATALOG_BASELINE, predecessorSql = undefined) {
  const { v4: v4Seal, v5: v5Seal } = HISTORICAL_REGISTRY_SEALS;
  const v6Digest = registryDigestFor(REGISTRY_V6_VERSION, rows, dbCatalogBaseline);
  const v6CatalogCount = dbCatalogBaseline.secdef_execute.count +
    dbCatalogBaseline.relation_dml.count + dbCatalogBaseline.column_dml.count;
  const v6EntryCount = rows.length + v6CatalogCount;
  const v5Path = "migrations/0461_siep15_forward_mutation_registry.sql";
  const v5Sql = predecessorSql ?? renderSIEP15RegistrySql(
    frozenInventory(REGISTRY_V5_VERSION), SIEP15_DB_CATALOG_BASELINE);
  const observedV5Sha = sha256(v5Sql);
  if (observedV5Sha !== HISTORICAL_REGISTRY_ARTIFACT_SHA256[v5Path])
    throw new Error(`sealed historical SCAC v5 migration changed: ${observedV5Sha}`);
  let sql = v5Sql
    .replaceAll(JSON.stringify(SIEP15_DB_CATALOG_BASELINE), JSON.stringify(dbCatalogBaseline))
    .replaceAll(
      `if observed_count<>${SIEP15_DB_CATALOG_BASELINE.secdef_execute.count} or observed_digest<>'${SIEP15_DB_CATALOG_BASELINE.secdef_execute.digest}' then return false; end if;`,
      `if observed_count<>${dbCatalogBaseline.secdef_execute.count} or observed_digest<>'${dbCatalogBaseline.secdef_execute.digest}' then return false; end if;`,
    )
    .replaceAll(
      `return observed_count=${SIEP15_DB_CATALOG_BASELINE.role_authority.count} and observed_digest='${SIEP15_DB_CATALOG_BASELINE.role_authority.digest}';`,
      `return observed_count=${dbCatalogBaseline.role_authority.count} and observed_digest='${dbCatalogBaseline.role_authority.digest}';`,
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
  dbCatalogBaseline = SIEP16_INTEGRATED_DB_CATALOG_BASELINE,
  predecessorSql = undefined) {
  const { v5: v5Seal, v6: v6Seal } = HISTORICAL_REGISTRY_SEALS;
  const v7Digest = registryDigestFor(REGISTRY_V7_VERSION, rows, dbCatalogBaseline);
  const v7CatalogCount = dbCatalogBaseline.secdef_execute.count +
    dbCatalogBaseline.relation_dml.count + dbCatalogBaseline.column_dml.count;
  const v7EntryCount = rows.length + v7CatalogCount;
  const v6Path = "migrations/0462_siep16_forward_mutation_registry.sql";
  const v6Sql = predecessorSql ?? renderSIEP16RegistrySql(
    frozenInventory(REGISTRY_V6_VERSION), SIEP16_DB_CATALOG_BASELINE);
  const observedV6Sha = sha256(v6Sql);
  if (observedV6Sha !== HISTORICAL_REGISTRY_ARTIFACT_SHA256[v6Path])
    throw new Error(`sealed historical SCAC v6 migration changed: ${observedV6Sha}`);
  let sql = v6Sql
    .replaceAll(JSON.stringify(SIEP16_DB_CATALOG_BASELINE), JSON.stringify(dbCatalogBaseline))
    .replaceAll(
      `if observed_count<>${SIEP16_DB_CATALOG_BASELINE.secdef_execute.count} or observed_digest<>'${SIEP16_DB_CATALOG_BASELINE.secdef_execute.digest}' then return false; end if;`,
      `if observed_count<>${dbCatalogBaseline.secdef_execute.count} or observed_digest<>'${dbCatalogBaseline.secdef_execute.digest}' then return false; end if;`,
    )
    .replaceAll(
      `return observed_count=${SIEP16_DB_CATALOG_BASELINE.role_authority.count} and observed_digest='${SIEP16_DB_CATALOG_BASELINE.role_authority.digest}';`,
      `return observed_count=${dbCatalogBaseline.role_authority.count} and observed_digest='${dbCatalogBaseline.role_authority.digest}';`,
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
  dbCatalogBaseline = SIEP17_FORWARD_DB_CATALOG_BASELINE,
  predecessorArtifacts = undefined) {
  const { v7: v7Seal } = HISTORICAL_REGISTRY_SEALS;
  const v8Digest = registryDigestFor(REGISTRY_V8_VERSION, rows, dbCatalogBaseline);
  const catalogCount = dbCatalogBaseline.secdef_execute.count +
    dbCatalogBaseline.relation_dml.count + dbCatalogBaseline.column_dml.count;
  const entryCount = rows.length + catalogCount;
  const v7MigrationPath = "migrations/0464_siep16_integrated_mutation_registry.sql";
  const v7RuntimePath = "mcp-server/src/scac-mutation-registry.v7.generated.js";
  const v7Rows = frozenInventory(REGISTRY_V7_VERSION);
  const v7Migration = predecessorArtifacts?.migration ??
    renderSIEP16IntegratedRegistrySql(v7Rows, SIEP16_INTEGRATED_DB_CATALOG_BASELINE);
  const v7Runtime = predecessorArtifacts?.runtime ?? renderRuntimeProjection(v7Rows, {
    version: REGISTRY_V7_VERSION, dbCatalogBaseline: SIEP16_INTEGRATED_DB_CATALOG_BASELINE,
  });
  for (const [path, source] of [
    [v7MigrationPath, v7Migration], [v7RuntimePath, v7Runtime],
  ]) {
    const observed = sha256(source);
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
      `if observed_count<>${dbCatalogBaseline.secdef_execute.count} or observed_digest<>'${dbCatalogBaseline.secdef_execute.digest}' then return false; end if;`)
    .replace(`return observed_count=${SIEP16_INTEGRATED_DB_CATALOG_BASELINE.role_authority.count} and observed_digest='${SIEP16_INTEGRATED_DB_CATALOG_BASELINE.role_authority.digest}';`,
      `return observed_count=${dbCatalogBaseline.role_authority.count} and observed_digest='${dbCatalogBaseline.role_authority.digest}';`);
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
  dbCatalogBaseline = SIEP18_FORWARD_DB_CATALOG_BASELINE,
  predecessorArtifacts = undefined) {
  const { v8: v8Seal } = HISTORICAL_REGISTRY_SEALS;
  const v9Digest = registryDigestFor(REGISTRY_V9_VERSION, rows, dbCatalogBaseline);
  const catalogCount = dbCatalogBaseline.secdef_execute.count +
    dbCatalogBaseline.relation_dml.count + dbCatalogBaseline.column_dml.count;
  const entryCount = rows.length + catalogCount;
  const v8MigrationPath = "migrations/0466_siep17_forward_mutation_registry.sql";
  const v8RuntimePath = "mcp-server/src/scac-mutation-registry.v8.generated.js";
  const v8Rows = frozenInventory(REGISTRY_V8_VERSION);
  const v8Migration = predecessorArtifacts?.migration ??
    renderSIEP17ForwardRegistrySql(v8Rows, SIEP17_FORWARD_DB_CATALOG_BASELINE);
  const v8Runtime = predecessorArtifacts?.runtime ?? renderRuntimeProjection(v8Rows, {
    version: REGISTRY_V8_VERSION, dbCatalogBaseline: SIEP17_FORWARD_DB_CATALOG_BASELINE,
  });
  for (const [path, source] of [
    [v8MigrationPath, v8Migration], [v8RuntimePath, v8Runtime],
  ]) {
    const observed = sha256(source);
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
  let v9Current = replaceExactlyOnce(
    v8Current
      .replaceAll("scac_mutation_catalog_v8_current", "scac_mutation_catalog_v9_current")
      .replaceAll("scac-mutation-registry.v8", "scac-mutation-registry.v9"),
    `if observed_count<>${SIEP17_FORWARD_DB_CATALOG_BASELINE.secdef_execute.count} or observed_digest<>'${SIEP17_FORWARD_DB_CATALOG_BASELINE.secdef_execute.digest}' then return false; end if;`,
    `if observed_count<>${dbCatalogBaseline.secdef_execute.count} or observed_digest<>'${dbCatalogBaseline.secdef_execute.digest}' then return false; end if;`,
    "SIEP-18 v9 current catalog baseline",
  );
  v9Current = replaceExactlyOnce(v9Current,
    `return observed_count=${SIEP17_FORWARD_DB_CATALOG_BASELINE.role_authority.count} and observed_digest='${SIEP17_FORWARD_DB_CATALOG_BASELINE.role_authority.digest}';`,
    `return observed_count=${dbCatalogBaseline.role_authority.count} and observed_digest='${dbCatalogBaseline.role_authority.digest}';`,
    "SIEP-18 v9 role-authority baseline");

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

  const monitorPath = "migrations/0467_siep18_atomic_db_monitor_grants.sql";
  const monitorFixture = directMigrationPreimage(monitorPath);
  const monitorMigration = predecessorArtifacts?.monitor ??
    renderDirectRegistryRedefinition(monitorFixture.preimage, {
      ownerExclusion: monitorFixture.owner_exclusion,
    });
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
  dbCatalogBaseline = SOURCE_MERGE_FORWARD_DB_CATALOG_BASELINE,
  predecessorArtifacts = undefined) {
  const { v9: v9Seal } = HISTORICAL_REGISTRY_SEALS;
  const v10Digest = registryDigestFor(REGISTRY_V10_VERSION, rows, dbCatalogBaseline);
  const catalogCount = dbCatalogBaseline.secdef_execute.count +
    dbCatalogBaseline.relation_dml.count + dbCatalogBaseline.column_dml.count;
  const entryCount = rows.length + catalogCount;
  const v9MigrationPath = "migrations/0468_siep18_forward_mutation_registry.sql";
  const v9RuntimePath = "mcp-server/src/scac-mutation-registry.v9.generated.js";
  const v9Rows = frozenInventory(REGISTRY_V9_VERSION);
  const v9Migration = predecessorArtifacts?.migration ??
    renderSIEP18ForwardRegistrySql(v9Rows, SIEP18_FORWARD_DB_CATALOG_BASELINE);
  const v9Runtime = predecessorArtifacts?.runtime ?? renderRuntimeProjection(v9Rows, {
    version: REGISTRY_V9_VERSION, dbCatalogBaseline: SIEP18_FORWARD_DB_CATALOG_BASELINE,
  });
  for (const [path, source] of [
    [v9MigrationPath, v9Migration], [v9RuntimePath, v9Runtime],
  ]) {
    const observed = sha256(source);
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
  let v10Current = replaceExactlyOnce(
    v9Current
      .replaceAll("scac_mutation_catalog_v9_current", "scac_mutation_catalog_v10_current")
      .replaceAll("scac-mutation-registry.v9", "scac-mutation-registry.v10"),
    `if observed_count<>${SIEP18_FORWARD_DB_CATALOG_BASELINE.secdef_execute.count} or observed_digest<>'${SIEP18_FORWARD_DB_CATALOG_BASELINE.secdef_execute.digest}' then return false; end if;`,
    `if observed_count<>${dbCatalogBaseline.secdef_execute.count} or observed_digest<>'${dbCatalogBaseline.secdef_execute.digest}' then return false; end if;`,
    "source-merge v10 current catalog baseline",
  );
  // WR-000048: the portable-scope census narrowing and the carr_-escalation
  // guard (no carve-out) are now authored once, at the chain root (v3's
  // shared template in renderSIEP13RegistrySql), and carried forward through
  // every version's cascade unchanged, exactly like the ACL categories. v9Core
  // (read from the freshly regenerated 0468 above) therefore already carries
  // both the narrowed scope and the guard; only the role-authority VALUE
  // differs per version, so that is the only substitution still needed here.
  v10Current = replaceExactlyOnce(v10Current,
    `return observed_count=${SIEP18_FORWARD_DB_CATALOG_BASELINE.role_authority.count} and observed_digest='${SIEP18_FORWARD_DB_CATALOG_BASELINE.role_authority.digest}';`,
    `return observed_count=${dbCatalogBaseline.role_authority.count} and observed_digest='${dbCatalogBaseline.role_authority.digest}';`,
    "source-merge v10 role-authority baseline");

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
  // 0471 is the frozen fourth link in the delivery-map sequence. The live
  // overlay has advanced again, but 0478 owns that later 6d21 -> eebfa repin.
  const successorMapDigest = "6d21c37d533a5d98debfe4991c902164cf3c1fee88e7f42a3112468268e3335c";
  const ruleMapRepinSql = successorMapDigest === priorMapDigest ? "" :
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
`  update ops.rule_delivery_activation_target set map_digest='${successorMapDigest}' where map_digest='${priorMapDigest}';\n` +
`  get diagnostics updated=row_count;\n` +
`  if updated<>8 then raise exception '0471 REFUSED: expected eight exact rule-delivery target repins, changed %',updated; end if;\n` +
`end $rule_map_repin$;\n`;
  return `${predecessorCatalogPreflight}${controlSql}\n${ruleMapRepinSql}${sql}`.replace(/\n+$/, "\n");
}

export function renderCodexContinuityForwardRegistrySql(rows = fullInventory(),
  dbCatalogBaseline = CODEX_CONTINUITY_FORWARD_DB_CATALOG_BASELINE,
  predecessorArtifacts = undefined) {
  const { v10: v10Seal } = HISTORICAL_REGISTRY_SEALS;
  const v11Digest = registryDigestFor(REGISTRY_V11_VERSION, rows, dbCatalogBaseline);
  const catalogCount = dbCatalogBaseline.secdef_execute.count +
    dbCatalogBaseline.relation_dml.count + dbCatalogBaseline.column_dml.count;
  const entryCount = rows.length + catalogCount;
  const v10MigrationPath = "migrations/0471_source_merge_catalog_registry_successor.sql";
  const v10RuntimePath = "mcp-server/src/scac-mutation-registry.v10.generated.js";
  const v10Rows = frozenInventory(REGISTRY_V10_VERSION);
  const v10Migration = predecessorArtifacts?.migration ??
    renderSourceMergeForwardRegistrySql(v10Rows, SOURCE_MERGE_FORWARD_DB_CATALOG_BASELINE);
  const v10Runtime = predecessorArtifacts?.runtime ?? renderRuntimeProjection(v10Rows, {
    version: REGISTRY_V10_VERSION, dbCatalogBaseline: SOURCE_MERGE_FORWARD_DB_CATALOG_BASELINE,
  });
  for (const [path, source] of [
    [v10MigrationPath, v10Migration], [v10RuntimePath, v10Runtime],
  ]) {
    const observed = sha256(source);
    if (observed !== HISTORICAL_REGISTRY_ARTIFACT_SHA256[path])
      throw new Error(`sealed historical SCAC v10 artifact changed: ${path}: ${observed}`);
  }

  const headerMarker = "-- SCAC-09: forward-only mutation registry v10 after source-merge authority projection.";
  const coreStart = v10Migration.indexOf(headerMarker);
  if (coreStart < 0 || v10Migration.indexOf(headerMarker, coreStart + headerMarker.length) >= 0)
    throw new Error("sealed SCAC v10 migration has no exact successor core boundary");
  const v10Core = v10Migration.slice(coreStart);
  const currentV10Marker = "create or replace function ops.scac_mutation_catalog_v10_current()";
  const policyMarker =
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v9;";
  const currentV10Start = v10Core.indexOf(currentV10Marker);
  const secondCurrentV10 = v10Core.indexOf(
    currentV10Marker, currentV10Start + currentV10Marker.length);
  const v9HistoryMarker =
    "alter function ops.scac_mutation_catalog_v9_current() rename to scac_mutation_catalog_v9_live_at_seal;";
  const v9HistoryStart = v10Core.indexOf(v9HistoryMarker);
  const secondV9History = v10Core.indexOf(
    v9HistoryMarker, v9HistoryStart + v9HistoryMarker.length);
  const policyStart = v10Core.indexOf(policyMarker);
  const secondPolicy = v10Core.indexOf(policyMarker, policyStart + policyMarker.length);
  if (v9HistoryStart < 0 || secondV9History >= 0 || currentV10Start <= v9HistoryStart ||
      secondCurrentV10 >= 0 || policyStart <= currentV10Start || secondPolicy >= 0)
    throw new Error("sealed SCAC v10 migration has no exact catalog successor boundary");
  const installedV9History = v10Core.slice(v9HistoryStart, currentV10Start);
  const v10Current = v10Core.slice(currentV10Start, policyStart);
  const v10History =
`alter function ops.scac_mutation_catalog_v10_current() rename to scac_mutation_catalog_v10_live_at_seal;
create or replace function ops.scac_mutation_registry_v10_seal_available()
returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$
  select ops.scac_mutation_registry_seal_valid('scac-mutation-registry.v10')
$fn$;
create or replace function ops.scac_mutation_catalog_v10_current()
returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$
  select ops.scac_mutation_catalog_v10_live_at_seal()
$fn$;
comment on function ops.scac_mutation_registry_v10_seal_available() is 'Exact immutable v10 registry seal; separate from whether the live catalog still equals v10.';
comment on function ops.scac_mutation_catalog_v10_current() is 'Historical v10 live-catalog validator; expected to become false after the v11 authority surface is installed.';

`;
  const renderV11Current = baseline => {
    let current = v10Current
      .replaceAll("scac_mutation_catalog_v10_current", "scac_mutation_catalog_v11_current")
      .replaceAll("scac-mutation-registry.v10", "scac-mutation-registry.v11");
    current = replaceExactlyOnce(current,
      `if observed_count<>${SOURCE_MERGE_FORWARD_DB_CATALOG_BASELINE.secdef_execute.count} or observed_digest<>'${SOURCE_MERGE_FORWARD_DB_CATALOG_BASELINE.secdef_execute.digest}' then return false; end if;`,
      `if observed_count<>${baseline.secdef_execute.count} or observed_digest<>'${baseline.secdef_execute.digest}' then return false; end if;`,
      "Codex continuity v11 security-definer baseline");
    current = replaceExactlyOnce(current,
      `if observed_count<>${SOURCE_MERGE_FORWARD_DB_CATALOG_BASELINE.relation_dml.count} or observed_digest<>'${SOURCE_MERGE_FORWARD_DB_CATALOG_BASELINE.relation_dml.digest}' then return false; end if;`,
      `if observed_count<>${baseline.relation_dml.count} or observed_digest<>'${baseline.relation_dml.digest}' then return false; end if;`,
      "Codex continuity v11 relation baseline");
    current = replaceExactlyOnce(current,
      `if observed_count<>${SOURCE_MERGE_FORWARD_DB_CATALOG_BASELINE.column_dml.count} or observed_digest<>'${SOURCE_MERGE_FORWARD_DB_CATALOG_BASELINE.column_dml.digest}' then return false; end if;`,
      `if observed_count<>${baseline.column_dml.count} or observed_digest<>'${baseline.column_dml.digest}' then return false; end if;`,
      "Codex continuity v11 column baseline");
    return replaceExactlyOnce(current,
      `return observed_count=${SOURCE_MERGE_FORWARD_DB_CATALOG_BASELINE.role_authority.count} and observed_digest='${SOURCE_MERGE_FORWARD_DB_CATALOG_BASELINE.role_authority.digest}';`,
      `return observed_count=${baseline.role_authority.count} and observed_digest='${baseline.role_authority.digest}';`,
      "Codex continuity v11 role-authority baseline");
  };
  const v11Current = renderV11Current(dbCatalogBaseline);

  let sql = replaceExactlyOnce(v10Core, v10Current,
    "__CODEX_CONTINUITY_V10_CATALOG_SUCCESSOR__", "Codex continuity v10 current catalog block");
  sql = replaceExactlyOnce(sql, installedV9History, "",
    "Codex continuity already-installed v9 catalog history");
  sql = replaceExactlyOnce(sql, headerMarker,
    "-- SCAC-10: forward-only mutation registry v11 after Codex continuity activation.",
    "Codex continuity migration header");
  sql = sql
    .replaceAll("scac-mutation-registry.v10", "scac-mutation-registry.v11")
    .replaceAll("_v10", "_v11")
    .replaceAll(" v10", " v11");
  sql = replaceExactlyOnce(sql, JSON.stringify(SOURCE_MERGE_FORWARD_DB_CATALOG_BASELINE),
    JSON.stringify(dbCatalogBaseline), "Codex continuity v11 catalog projection");
  sql = replaceExactlyOnce(sql,
    `'${v10Seal.digest}',${v10Seal.entryCount},${v10Seal.sourceEntryCount},`,
    `'sha256:${v11Digest}',${entryCount},${rows.length},`,
    "Codex continuity v11 registry row");
  sql = replaceExactlyOnce(sql,
    `ops.scac_mutation_registration_v11('${v10Seal.digest}',`,
    `ops.scac_mutation_registration_v11('sha256:${v11Digest}',`,
    "Codex continuity v11 snapshot registry lookup");
  sql = replaceExactlyOnce(sql,
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v9;",
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v10;",
    "Codex continuity policy snapshot predecessor");
  sql = replaceExactlyOnce(sql, "__CODEX_CONTINUITY_V10_CATALOG_SUCCESSOR__",
    `${v10History}${v11Current}`, "Codex continuity v10 catalog history insertion");

  const v10CatalogJson = JSON.stringify(SOURCE_MERGE_FORWARD_DB_CATALOG_BASELINE);
  sql = replaceExactlyOnce(sql,
    "check (registry_version in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v4','scac-mutation-registry.v5','scac-mutation-registry.v6','scac-mutation-registry.v7','scac-mutation-registry.v8','scac-mutation-registry.v9','scac-mutation-registry.v11'))",
    "check (registry_version in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v4','scac-mutation-registry.v5','scac-mutation-registry.v6','scac-mutation-registry.v7','scac-mutation-registry.v8','scac-mutation-registry.v9','scac-mutation-registry.v10','scac-mutation-registry.v11'))",
    "Codex continuity registry-version constraint");
  sql = replaceExactlyOnce(sql,
    "if p_registry_version not in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v4','scac-mutation-registry.v5','scac-mutation-registry.v6','scac-mutation-registry.v7','scac-mutation-registry.v8','scac-mutation-registry.v9') then return false; end if;",
    "if p_registry_version not in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v4','scac-mutation-registry.v5','scac-mutation-registry.v6','scac-mutation-registry.v7','scac-mutation-registry.v8','scac-mutation-registry.v9','scac-mutation-registry.v10') then return false; end if;",
    "Codex continuity historical seal allowlist");
  sql = replaceExactlyOnce(sql,
    `    when 'scac-mutation-registry.v9' then '${HISTORICAL_REGISTRY_SEALS.v9.digest}' end;`,
    `    when 'scac-mutation-registry.v9' then '${HISTORICAL_REGISTRY_SEALS.v9.digest}'\n    when '${v10Seal.version}' then '${v10Seal.digest}' end;`,
    "Codex continuity historical digest case");
  sql = replaceExactlyOnce(sql,
    `    when 'scac-mutation-registry.v9' then '${JSON.stringify(SIEP18_FORWARD_DB_CATALOG_BASELINE)}'::jsonb end;`,
    `    when 'scac-mutation-registry.v9' then '${JSON.stringify(SIEP18_FORWARD_DB_CATALOG_BASELINE)}'::jsonb\n    when '${v10Seal.version}' then '${v10CatalogJson}'::jsonb end;`,
    "Codex continuity historical catalog case");
  sql = replaceExactlyOnce(sql,
    `    ('scac-mutation-registry.v9','${HISTORICAL_REGISTRY_SEALS.v9.digest}',${HISTORICAL_REGISTRY_SEALS.v9.entryCount},${HISTORICAL_REGISTRY_SEALS.v9.sourceEntryCount})\n`,
    `    ('scac-mutation-registry.v9','${HISTORICAL_REGISTRY_SEALS.v9.digest}',${HISTORICAL_REGISTRY_SEALS.v9.entryCount},${HISTORICAL_REGISTRY_SEALS.v9.sourceEntryCount}),\n    ('${v10Seal.version}','${v10Seal.digest}',${v10Seal.entryCount},${v10Seal.sourceEntryCount})\n`,
    "Codex continuity historical seal tuple");
  sql = replaceExactlyOnce(sql,
    "    ops.scac_mutation_registry_v9_seal_available()) then",
    "    ops.scac_mutation_registry_v9_seal_available() and\n    ops.scac_mutation_registry_v10_seal_available()) then",
    "Codex continuity snapshot predecessor seal");
  sql = replaceExactlyOnce(sql,
    `or (r.registry_version='scac-mutation-registry.v11' and r.registry_digest='${v10Seal.digest}')`,
    `or (r.registry_version='scac-mutation-registry.v10' and r.registry_digest='${v10Seal.digest}')\n         or (r.registry_version='scac-mutation-registry.v11' and r.registry_digest='sha256:${v11Digest}')`,
    "Codex continuity epoch-chain digest cases");
  sql = replaceExactlyOnce(sql,
    `  (registry_version='scac-mutation-registry.v11' and registry_digest='${v10Seal.digest}')`,
    `  (registry_version='scac-mutation-registry.v10' and registry_digest='${v10Seal.digest}') or\n  (registry_version='scac-mutation-registry.v11' and registry_digest='sha256:${v11Digest}')`,
    "Codex continuity epoch constraint digest cases");
  sql = replaceExactlyOnce(sql,
    `'{registry_digest}',to_jsonb('${v10Seal.digest}'::text)`,
    `'{registry_digest}',to_jsonb('sha256:${v11Digest}'::text)`,
    "Codex continuity snapshot registry digest");
  sql = replaceExactlyOnce(sql,
    "ops.scac_mutation_registry_v9_seal_available(),ops.scac_mutation_catalog_v11_current()",
    "ops.scac_mutation_registry_v9_seal_available(),ops.scac_mutation_catalog_v10_live_at_seal(),ops.scac_mutation_catalog_v10_current(),ops.scac_mutation_registry_v10_seal_available(),ops.scac_mutation_catalog_v11_current()",
    "Codex continuity historical function revoke list");
  sql = replaceExactlyOnce(sql,
    "Source-merge successor snapshot: current policy epochs bind mutation registry v11 while historical v2/v3/v4/v5/v6/v7/v8/v9 epochs remain immutable.",
    "Codex continuity successor snapshot: current policy epochs bind mutation registry v11 while historical v2/v3/v4/v5/v6/v7/v8/v9/v10 epochs remain immutable.",
    "Codex continuity policy snapshot comment");
  sql = replaceExactlyOnce(sql,
    `(select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v11')<>${v10Seal.entryCount}`,
    `(select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v11')<>${entryCount}`,
    "Codex continuity v11 entry count guard");
  sql = replaceExactlyOnce(sql,
    `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v9')<>'${HISTORICAL_REGISTRY_SEALS.v9.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v9')<>${HISTORICAL_REGISTRY_SEALS.v9.entryCount} then raise exception 'sealed SCAC mutation registry v9 changed during successor creation'; end if;`,
    `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v10')<>'${v10Seal.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v10')<>${v10Seal.entryCount} then raise exception 'sealed SCAC mutation registry v10 changed during successor creation'; end if;`,
    "Codex continuity predecessor seal guard");
  sql = replaceExactlyOnce(sql,
    "ops.scac_policy_epoch_snapshot(),ops.scac_policy_epoch_snapshot_v6(),ops.scac_policy_epoch_snapshot_v7(),ops.scac_policy_epoch_snapshot_v8(),ops.scac_policy_epoch_snapshot_v9(),",
    "ops.scac_policy_epoch_snapshot(),ops.scac_policy_epoch_snapshot_v6(),ops.scac_policy_epoch_snapshot_v7(),ops.scac_policy_epoch_snapshot_v8(),ops.scac_policy_epoch_snapshot_v9(),ops.scac_policy_epoch_snapshot_v10(),",
    "Codex continuity historical policy snapshot revoke list");

  const seedStartMarker = "with seed as (select value as contract from jsonb_array_elements(";
  const seedEndMarker = "::jsonb))\ninsert into ops.scac_mutation_registry_entry";
  const seedStart = sql.indexOf(seedStartMarker);
  const secondSeedStart = sql.indexOf(seedStartMarker, seedStart + seedStartMarker.length);
  const seedEnd = sql.indexOf(seedEndMarker, seedStart + seedStartMarker.length);
  const secondSeedEnd = sql.indexOf(seedEndMarker, seedEnd + seedEndMarker.length);
  if (seedStart < 0 || secondSeedStart >= 0 || seedEnd < 0 || secondSeedEnd >= 0)
    throw new Error("sealed SCAC v10 migration has no exact source-seed boundary");
  const seed = JSON.stringify(rows.map(row => ({ ...row, entry_digest: `sha256:${sha256(row)}` })));
  sql = `${sql.slice(0, seedStart + seedStartMarker.length)}${sqlLiteral(seed)}${sql.slice(seedEnd)}`;
  sql = replaceExactlyOnce(sql,
    `(grant_snapshot->>'entry_count')::integer=${SOURCE_MERGE_FORWARD_DB_CATALOG_BASELINE.runtime_dml_grants.count} and\n    grant_snapshot->>'grant_digest'='${SOURCE_MERGE_FORWARD_DB_CATALOG_BASELINE.runtime_dml_grants.digest}'`,
    `(grant_snapshot->>'entry_count')::integer=${dbCatalogBaseline.runtime_dml_grants.count} and\n    grant_snapshot->>'grant_digest'='${dbCatalogBaseline.runtime_dml_grants.digest}'`,
    "Codex continuity exact runtime grant binding");

  const preflightCurrent = renderV11Current(CODEX_CONTINUITY_PRE_V11_DB_CATALOG_BASELINE);
  const preflightBegin = preflightCurrent.indexOf("begin\n");
  const preflightEnd = preflightCurrent.lastIndexOf("end $fn$;");
  if (preflightBegin < 0 || preflightEnd <= preflightBegin)
    throw new Error("generated v11 catalog predicate has no exact preflight body boundary");
  let preflightBody = preflightCurrent.slice(preflightBegin + "begin\n".length, preflightEnd);
  preflightBody = preflightBody.replaceAll(
    "then return false; end if;",
    "then raise exception 'Codex continuity pre-v11 catalog receipt drifted'; end if;");
  preflightBody = replaceExactlyOnce(preflightBody,
    `return observed_count=${CODEX_CONTINUITY_PRE_V11_DB_CATALOG_BASELINE.role_authority.count} and observed_digest='${CODEX_CONTINUITY_PRE_V11_DB_CATALOG_BASELINE.role_authority.digest}';`,
    `if observed_count<>${CODEX_CONTINUITY_PRE_V11_DB_CATALOG_BASELINE.role_authority.count} or observed_digest<>'${CODEX_CONTINUITY_PRE_V11_DB_CATALOG_BASELINE.role_authority.digest}' then raise exception 'Codex continuity pre-v11 role-authority receipt drifted'; end if;`,
    "Codex continuity pre-v11 role receipt");
  const predecessorPreflight =
`-- Exact disposable-PG17 post-0480 receipt. Refuse before creating any v11 function.
do $codex_continuity_preflight$
declare observed_count integer; observed_digest text; grant_snapshot jsonb;
begin
  if (select count(*) from public.schema_migrations where filename='0480_codex_continuity.sql')<>1
     or not exists(select 1 from public.schema_migrations where filename='0480_codex_continuity.sql'
       and sha256='c1451a6c94b3be00f4099a83aa9519dee352fcc2cd0f198323696d2f42088aa4') then
    raise exception 'Codex continuity pre-v11 migration ledger receipt drifted';
  end if;
  grant_snapshot:=ops.scac_runtime_dml_grant_snapshot();
  if (grant_snapshot->>'entry_count')::integer<>${CODEX_CONTINUITY_PRE_V11_DB_CATALOG_BASELINE.runtime_dml_grants.count}
     or grant_snapshot->>'grant_digest'<>'${CODEX_CONTINUITY_PRE_V11_DB_CATALOG_BASELINE.runtime_dml_grants.digest}' then
    raise exception 'Codex continuity pre-v11 runtime grant receipt drifted';
  end if;
${preflightBody}end $codex_continuity_preflight$;

`;
  const continuityGuardBinding =
`-- Bind every newly writable continuity relation into the existing atomic monitor.
do $codex_continuity_monitor_guards$
declare relation_name text; relation_oid regclass;
begin
  foreach relation_name in array array['codex_continuity_checkpoint','codex_continuity_revision','codex_continuity_event'] loop
    relation_oid:=to_regclass('public.'||relation_name);
    if relation_oid is null then
      raise exception 'Codex continuity monitor relation is unavailable: %',relation_name;
    end if;
    if exists(select 1 from pg_trigger where tgrelid=relation_oid and not tgisinternal
      and tgfoid='ops.scac_reference_monitor_guard()'::regprocedure) then
      raise exception 'Codex continuity monitor relation was already bound: %',relation_name;
    end if;
    execute format('create trigger scac_reference_monitor_guard_row before insert or update or delete on public.%I for each row execute function ops.scac_reference_monitor_guard()',relation_name);
    execute format('create trigger scac_reference_monitor_guard_truncate before truncate on public.%I for each statement execute function ops.scac_reference_monitor_guard()',relation_name);
  end loop;
end $codex_continuity_monitor_guards$;

`;
  return `${predecessorPreflight}${continuityGuardBinding}${sql}`.replace(/\n+$/, "\n");
}

export function renderClaudeContinuityForwardRegistrySql(rows = fullInventory(),
  dbCatalogBaseline = CLAUDE_CONTINUITY_FORWARD_DB_CATALOG_BASELINE,
  predecessorArtifacts = undefined) {
  const { v11: v11Seal } = HISTORICAL_REGISTRY_SEALS;
  const v12Digest = registryDigestFor(REGISTRY_V12_VERSION, rows, dbCatalogBaseline);
  const catalogCount = dbCatalogBaseline.secdef_execute.count +
    dbCatalogBaseline.relation_dml.count + dbCatalogBaseline.column_dml.count;
  const entryCount = rows.length + catalogCount;
  const v11MigrationPath = "migrations/0481_codex_continuity_registry_activation.sql";
  const v11RuntimePath = "mcp-server/src/scac-mutation-registry.v11.generated.js";
  const v11Rows = frozenInventory(REGISTRY_V11_VERSION);
  const v11Migration = predecessorArtifacts?.migration ??
    renderCodexContinuityForwardRegistrySql(v11Rows, CODEX_CONTINUITY_FORWARD_DB_CATALOG_BASELINE);
  const v11Runtime = predecessorArtifacts?.runtime ?? renderRuntimeProjection(v11Rows, {
    version: REGISTRY_V11_VERSION, dbCatalogBaseline: CODEX_CONTINUITY_FORWARD_DB_CATALOG_BASELINE,
  });
  for (const [path, source] of [
    [v11MigrationPath, v11Migration], [v11RuntimePath, v11Runtime],
  ]) {
    const observed = sha256(source);
    if (observed !== HISTORICAL_REGISTRY_ARTIFACT_SHA256[path])
      throw new Error(`sealed historical SCAC v11 artifact changed: ${path}: ${observed}`);
  }

  const headerMarker = "-- SCAC-10: forward-only mutation registry v11 after Codex continuity activation.";
  const coreStart = v11Migration.indexOf(headerMarker);
  if (coreStart < 0 || v11Migration.indexOf(headerMarker, coreStart + headerMarker.length) >= 0)
    throw new Error("sealed SCAC v11 migration has no exact successor core boundary");
  const v11Core = v11Migration.slice(coreStart);
  const currentV11Marker = "create or replace function ops.scac_mutation_catalog_v11_current()";
  const policyMarker =
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v10;";
  const currentV11Start = v11Core.indexOf(currentV11Marker);
  const secondCurrentV11 = v11Core.indexOf(
    currentV11Marker, currentV11Start + currentV11Marker.length);
  const v10HistoryMarker =
    "alter function ops.scac_mutation_catalog_v10_current() rename to scac_mutation_catalog_v10_live_at_seal;";
  const v10HistoryStart = v11Core.indexOf(v10HistoryMarker);
  const secondV10History = v11Core.indexOf(
    v10HistoryMarker, v10HistoryStart + v10HistoryMarker.length);
  const policyStart = v11Core.indexOf(policyMarker);
  const secondPolicy = v11Core.indexOf(policyMarker, policyStart + policyMarker.length);
  if (v10HistoryStart < 0 || secondV10History >= 0 || currentV11Start <= v10HistoryStart ||
      secondCurrentV11 >= 0 || policyStart <= currentV11Start || secondPolicy >= 0)
    throw new Error("sealed SCAC v11 migration has no exact catalog successor boundary");
  const installedV10History = v11Core.slice(v10HistoryStart, currentV11Start);
  const v11Current = v11Core.slice(currentV11Start, policyStart);
  const v11History =
`alter function ops.scac_mutation_catalog_v11_current() rename to scac_mutation_catalog_v11_live_at_seal;
create or replace function ops.scac_mutation_registry_v11_seal_available()
returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$
  select ops.scac_mutation_registry_seal_valid('scac-mutation-registry.v11')
$fn$;
create or replace function ops.scac_mutation_catalog_v11_current()
returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$
  select ops.scac_mutation_catalog_v11_live_at_seal()
$fn$;
comment on function ops.scac_mutation_registry_v11_seal_available() is 'Exact immutable v11 registry seal; separate from whether the live catalog still equals v11.';
comment on function ops.scac_mutation_catalog_v11_current() is 'Historical v11 live-catalog validator; expected to become false after the v12 authority surface is installed.';

`;
  const renderV12Current = baseline => {
    let current = v11Current
      .replaceAll("scac_mutation_catalog_v11_current", "scac_mutation_catalog_v12_current")
      .replaceAll("scac-mutation-registry.v11", "scac-mutation-registry.v12");
    current = replaceExactlyOnce(current,
      `if observed_count<>${CODEX_CONTINUITY_FORWARD_DB_CATALOG_BASELINE.secdef_execute.count} or observed_digest<>'${CODEX_CONTINUITY_FORWARD_DB_CATALOG_BASELINE.secdef_execute.digest}' then return false; end if;`,
      `if observed_count<>${baseline.secdef_execute.count} or observed_digest<>'${baseline.secdef_execute.digest}' then return false; end if;`,
      "Claude continuity v12 security-definer baseline");
    current = replaceExactlyOnce(current,
      `if observed_count<>${CODEX_CONTINUITY_FORWARD_DB_CATALOG_BASELINE.relation_dml.count} or observed_digest<>'${CODEX_CONTINUITY_FORWARD_DB_CATALOG_BASELINE.relation_dml.digest}' then return false; end if;`,
      `if observed_count<>${baseline.relation_dml.count} or observed_digest<>'${baseline.relation_dml.digest}' then return false; end if;`,
      "Claude continuity v12 relation baseline");
    current = replaceExactlyOnce(current,
      `if observed_count<>${CODEX_CONTINUITY_FORWARD_DB_CATALOG_BASELINE.column_dml.count} or observed_digest<>'${CODEX_CONTINUITY_FORWARD_DB_CATALOG_BASELINE.column_dml.digest}' then return false; end if;`,
      `if observed_count<>${baseline.column_dml.count} or observed_digest<>'${baseline.column_dml.digest}' then return false; end if;`,
      "Claude continuity v12 column baseline");
    return replaceExactlyOnce(current,
      `return observed_count=${CODEX_CONTINUITY_FORWARD_DB_CATALOG_BASELINE.role_authority.count} and observed_digest='${CODEX_CONTINUITY_FORWARD_DB_CATALOG_BASELINE.role_authority.digest}';`,
      `return observed_count=${baseline.role_authority.count} and observed_digest='${baseline.role_authority.digest}';`,
      "Claude continuity v12 role-authority baseline");
  };
  const v12Current = renderV12Current(dbCatalogBaseline);

  let sql = replaceExactlyOnce(v11Core, v11Current,
    "__CLAUDE_CONTINUITY_V11_CATALOG_SUCCESSOR__", "Claude continuity v11 current catalog block");
  sql = replaceExactlyOnce(sql, installedV10History, "",
    "Claude continuity already-installed v10 catalog history");
  sql = replaceExactlyOnce(sql, headerMarker,
    "-- SCAC-11: forward-only mutation registry v12 after Claude continuity activation.",
    "Claude continuity migration header");
  sql = sql
    .replaceAll("scac-mutation-registry.v11", "scac-mutation-registry.v12")
    .replaceAll("_v11", "_v12")
    .replaceAll(" v11", " v12");
  sql = replaceExactlyOnce(sql, JSON.stringify(CODEX_CONTINUITY_FORWARD_DB_CATALOG_BASELINE),
    JSON.stringify(dbCatalogBaseline), "Claude continuity v12 catalog projection");
  sql = replaceExactlyOnce(sql,
    `'${v11Seal.digest}',${v11Seal.entryCount},${v11Seal.sourceEntryCount},`,
    `'sha256:${v12Digest}',${entryCount},${rows.length},`,
    "Claude continuity v12 registry row");
  sql = replaceExactlyOnce(sql,
    `ops.scac_mutation_registration_v12('${v11Seal.digest}',`,
    `ops.scac_mutation_registration_v12('sha256:${v12Digest}',`,
    "Claude continuity v12 snapshot registry lookup");
  sql = replaceExactlyOnce(sql,
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v10;",
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v11;",
    "Claude continuity policy snapshot predecessor");
  sql = replaceExactlyOnce(sql, "__CLAUDE_CONTINUITY_V11_CATALOG_SUCCESSOR__",
    `${v11History}${v12Current}`, "Claude continuity v11 catalog history insertion");

  const v11CatalogJson = JSON.stringify(CODEX_CONTINUITY_FORWARD_DB_CATALOG_BASELINE);
  sql = replaceExactlyOnce(sql,
    "check (registry_version in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v4','scac-mutation-registry.v5','scac-mutation-registry.v6','scac-mutation-registry.v7','scac-mutation-registry.v8','scac-mutation-registry.v9','scac-mutation-registry.v10','scac-mutation-registry.v12'))",
    "check (registry_version in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v4','scac-mutation-registry.v5','scac-mutation-registry.v6','scac-mutation-registry.v7','scac-mutation-registry.v8','scac-mutation-registry.v9','scac-mutation-registry.v10','scac-mutation-registry.v11','scac-mutation-registry.v12'))",
    "Claude continuity registry-version constraint");
  sql = replaceExactlyOnce(sql,
    "if p_registry_version not in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v4','scac-mutation-registry.v5','scac-mutation-registry.v6','scac-mutation-registry.v7','scac-mutation-registry.v8','scac-mutation-registry.v9','scac-mutation-registry.v10') then return false; end if;",
    "if p_registry_version not in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v4','scac-mutation-registry.v5','scac-mutation-registry.v6','scac-mutation-registry.v7','scac-mutation-registry.v8','scac-mutation-registry.v9','scac-mutation-registry.v10','scac-mutation-registry.v11') then return false; end if;",
    "Claude continuity historical seal allowlist");
  sql = replaceExactlyOnce(sql,
    `    when 'scac-mutation-registry.v10' then '${HISTORICAL_REGISTRY_SEALS.v10.digest}' end;`,
    `    when 'scac-mutation-registry.v10' then '${HISTORICAL_REGISTRY_SEALS.v10.digest}'\n    when '${v11Seal.version}' then '${v11Seal.digest}' end;`,
    "Claude continuity historical digest case");
  sql = replaceExactlyOnce(sql,
    `    when 'scac-mutation-registry.v10' then '${JSON.stringify(SOURCE_MERGE_FORWARD_DB_CATALOG_BASELINE)}'::jsonb end;`,
    `    when 'scac-mutation-registry.v10' then '${JSON.stringify(SOURCE_MERGE_FORWARD_DB_CATALOG_BASELINE)}'::jsonb\n    when '${v11Seal.version}' then '${v11CatalogJson}'::jsonb end;`,
    "Claude continuity historical catalog case");
  sql = replaceExactlyOnce(sql,
    `    ('scac-mutation-registry.v10','${HISTORICAL_REGISTRY_SEALS.v10.digest}',${HISTORICAL_REGISTRY_SEALS.v10.entryCount},${HISTORICAL_REGISTRY_SEALS.v10.sourceEntryCount})\n`,
    `    ('scac-mutation-registry.v10','${HISTORICAL_REGISTRY_SEALS.v10.digest}',${HISTORICAL_REGISTRY_SEALS.v10.entryCount},${HISTORICAL_REGISTRY_SEALS.v10.sourceEntryCount}),\n    ('${v11Seal.version}','${v11Seal.digest}',${v11Seal.entryCount},${v11Seal.sourceEntryCount})\n`,
    "Claude continuity historical seal tuple");
  sql = replaceExactlyOnce(sql,
    "    ops.scac_mutation_registry_v10_seal_available()) then",
    "    ops.scac_mutation_registry_v10_seal_available() and\n    ops.scac_mutation_registry_v11_seal_available()) then",
    "Claude continuity snapshot predecessor seal");
  sql = replaceExactlyOnce(sql,
    `or (r.registry_version='scac-mutation-registry.v12' and r.registry_digest='${v11Seal.digest}')`,
    `or (r.registry_version='scac-mutation-registry.v11' and r.registry_digest='${v11Seal.digest}')\n         or (r.registry_version='scac-mutation-registry.v12' and r.registry_digest='sha256:${v12Digest}')`,
    "Claude continuity epoch-chain digest cases");
  sql = replaceExactlyOnce(sql,
    `  (registry_version='scac-mutation-registry.v12' and registry_digest='${v11Seal.digest}')`,
    `  (registry_version='scac-mutation-registry.v11' and registry_digest='${v11Seal.digest}') or\n  (registry_version='scac-mutation-registry.v12' and registry_digest='sha256:${v12Digest}')`,
    "Claude continuity epoch constraint digest cases");
  sql = replaceExactlyOnce(sql,
    `'{registry_digest}',to_jsonb('${v11Seal.digest}'::text)`,
    `'{registry_digest}',to_jsonb('sha256:${v12Digest}'::text)`,
    "Claude continuity snapshot registry digest");
  sql = replaceExactlyOnce(sql,
    "ops.scac_mutation_registry_v10_seal_available(),ops.scac_mutation_catalog_v12_current()",
    "ops.scac_mutation_registry_v10_seal_available(),ops.scac_mutation_catalog_v11_live_at_seal(),ops.scac_mutation_catalog_v11_current(),ops.scac_mutation_registry_v11_seal_available(),ops.scac_mutation_catalog_v12_current()",
    "Claude continuity historical function revoke list");
  sql = replaceExactlyOnce(sql,
    "Codex continuity successor snapshot: current policy epochs bind mutation registry v12 while historical v2/v3/v4/v5/v6/v7/v8/v9/v10 epochs remain immutable.",
    "Claude continuity successor snapshot: current policy epochs bind mutation registry v12 while historical v2/v3/v4/v5/v6/v7/v8/v9/v10/v11 epochs remain immutable.",
    "Claude continuity policy snapshot comment");
  sql = replaceExactlyOnce(sql,
    `(select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v12')<>${v11Seal.entryCount}`,
    `(select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v12')<>${entryCount}`,
    "Claude continuity v12 entry count guard");
  sql = replaceExactlyOnce(sql,
    `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v10')<>'${HISTORICAL_REGISTRY_SEALS.v10.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v10')<>${HISTORICAL_REGISTRY_SEALS.v10.entryCount} then raise exception 'sealed SCAC mutation registry v10 changed during successor creation'; end if;`,
    `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v11')<>'${v11Seal.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v11')<>${v11Seal.entryCount} then raise exception 'sealed SCAC mutation registry v11 changed during successor creation'; end if;`,
    "Claude continuity predecessor seal guard");
  sql = replaceExactlyOnce(sql,
    "ops.scac_policy_epoch_snapshot(),ops.scac_policy_epoch_snapshot_v6(),ops.scac_policy_epoch_snapshot_v7(),ops.scac_policy_epoch_snapshot_v8(),ops.scac_policy_epoch_snapshot_v9(),ops.scac_policy_epoch_snapshot_v10(),",
    "ops.scac_policy_epoch_snapshot(),ops.scac_policy_epoch_snapshot_v6(),ops.scac_policy_epoch_snapshot_v7(),ops.scac_policy_epoch_snapshot_v8(),ops.scac_policy_epoch_snapshot_v9(),ops.scac_policy_epoch_snapshot_v10(),ops.scac_policy_epoch_snapshot_v11(),",
    "Claude continuity historical policy snapshot revoke list");

  const seedStartMarker = "with seed as (select value as contract from jsonb_array_elements(";
  const seedEndMarker = "::jsonb))\ninsert into ops.scac_mutation_registry_entry";
  const seedStart = sql.indexOf(seedStartMarker);
  const secondSeedStart = sql.indexOf(seedStartMarker, seedStart + seedStartMarker.length);
  const seedEnd = sql.indexOf(seedEndMarker, seedStart + seedStartMarker.length);
  const secondSeedEnd = sql.indexOf(seedEndMarker, seedEnd + seedEndMarker.length);
  if (seedStart < 0 || secondSeedStart >= 0 || seedEnd < 0 || secondSeedEnd >= 0)
    throw new Error("sealed SCAC v11 migration has no exact source-seed boundary");
  const seed = JSON.stringify(rows.map(row => ({ ...row, entry_digest: `sha256:${sha256(row)}` })));
  sql = `${sql.slice(0, seedStart + seedStartMarker.length)}${sqlLiteral(seed)}${sql.slice(seedEnd)}`;
  sql = replaceExactlyOnce(sql,
    `(grant_snapshot->>'entry_count')::integer=${CODEX_CONTINUITY_FORWARD_DB_CATALOG_BASELINE.runtime_dml_grants.count} and\n    grant_snapshot->>'grant_digest'='${CODEX_CONTINUITY_FORWARD_DB_CATALOG_BASELINE.runtime_dml_grants.digest}'`,
    `(grant_snapshot->>'entry_count')::integer=${dbCatalogBaseline.runtime_dml_grants.count} and\n    grant_snapshot->>'grant_digest'='${dbCatalogBaseline.runtime_dml_grants.digest}'`,
    "Claude continuity exact runtime grant binding");

  const preflightCurrent = renderV12Current(CLAUDE_CONTINUITY_PRE_V12_DB_CATALOG_BASELINE);
  const preflightBegin = preflightCurrent.indexOf("begin\n");
  const preflightEnd = preflightCurrent.lastIndexOf("end $fn$;");
  if (preflightBegin < 0 || preflightEnd <= preflightBegin)
    throw new Error("generated v12 catalog predicate has no exact preflight body boundary");
  let preflightBody = preflightCurrent.slice(preflightBegin + "begin\n".length, preflightEnd);
  preflightBody = preflightBody.replaceAll(
    "then return false; end if;",
    "then raise exception 'Claude continuity pre-v12 catalog receipt drifted'; end if;");
  preflightBody = replaceExactlyOnce(preflightBody,
    `return observed_count=${CLAUDE_CONTINUITY_PRE_V12_DB_CATALOG_BASELINE.role_authority.count} and observed_digest='${CLAUDE_CONTINUITY_PRE_V12_DB_CATALOG_BASELINE.role_authority.digest}';`,
    `if observed_count<>${CLAUDE_CONTINUITY_PRE_V12_DB_CATALOG_BASELINE.role_authority.count} or observed_digest<>'${CLAUDE_CONTINUITY_PRE_V12_DB_CATALOG_BASELINE.role_authority.digest}' then raise exception 'Claude continuity pre-v12 role-authority receipt drifted'; end if;`,
    "Claude continuity pre-v12 role receipt");
  const predecessorPreflight =
`-- Exact disposable-PG17 post-0485 receipt. Refuse before creating any v12 function.
do $claude_continuity_preflight$
declare observed_count integer; observed_digest text; grant_snapshot jsonb;
begin
  if (select count(*) from public.schema_migrations where filename='0485_claude_continuity.sql')<>1
     or not exists(select 1 from public.schema_migrations where filename='0485_claude_continuity.sql'
       and sha256='d9fccd80e7cd63bedfdd4c1bdf0b431882735f6cc22ed7e1445c276d2d365322') then
    raise exception 'Claude continuity pre-v12 migration ledger receipt drifted';
  end if;
  grant_snapshot:=ops.scac_runtime_dml_grant_snapshot();
  if (grant_snapshot->>'entry_count')::integer<>${CLAUDE_CONTINUITY_PRE_V12_DB_CATALOG_BASELINE.runtime_dml_grants.count}
     or grant_snapshot->>'grant_digest'<>'${CLAUDE_CONTINUITY_PRE_V12_DB_CATALOG_BASELINE.runtime_dml_grants.digest}' then
    raise exception 'Claude continuity pre-v12 runtime grant receipt drifted';
  end if;
${preflightBody}end $claude_continuity_preflight$;

`;
  const continuityGuardBinding =
`-- Bind every newly writable continuity relation into the existing atomic monitor.
do $claude_continuity_monitor_guards$
declare relation_name text; relation_oid regclass;
begin
  foreach relation_name in array array['claude_continuity_leaf','claude_continuity_checkpoint','claude_continuity_revision','claude_continuity_event'] loop
    relation_oid:=to_regclass('public.'||relation_name);
    if relation_oid is null then
      raise exception 'Claude continuity monitor relation is unavailable: %',relation_name;
    end if;
    if exists(select 1 from pg_trigger where tgrelid=relation_oid and not tgisinternal
      and tgfoid='ops.scac_reference_monitor_guard()'::regprocedure) then
      raise exception 'Claude continuity monitor relation was already bound: %',relation_name;
    end if;
    execute format('create trigger scac_reference_monitor_guard_row before insert or update or delete on public.%I for each row execute function ops.scac_reference_monitor_guard()',relation_name);
    execute format('create trigger scac_reference_monitor_guard_truncate before truncate on public.%I for each statement execute function ops.scac_reference_monitor_guard()',relation_name);
  end loop;
end $claude_continuity_monitor_guards$;

`;
  return `${predecessorPreflight}${continuityGuardBinding}${sql}`.replace(/\n+$/, "\n");
}

export function renderClaudeStartupForwardRegistrySql(rows = fullInventory(),
  dbCatalogBaseline = CLAUDE_STARTUP_FORWARD_DB_CATALOG_BASELINE,
  predecessorArtifacts = undefined) {
  const { v12: v12Seal } = HISTORICAL_REGISTRY_SEALS;
  const v13Digest = registryDigestFor(REGISTRY_V13_VERSION, rows, dbCatalogBaseline);
  const catalogCount = dbCatalogBaseline.secdef_execute.count +
    dbCatalogBaseline.relation_dml.count + dbCatalogBaseline.column_dml.count;
  const entryCount = rows.length + catalogCount;
  const v12MigrationPath = "migrations/0486_claude_continuity_registry_activation.sql";
  const v12RuntimePath = "mcp-server/src/scac-mutation-registry.v12.generated.js";
  const v12Rows = frozenInventory(REGISTRY_V12_VERSION);
  const v12Migration = predecessorArtifacts?.migration ??
    renderClaudeContinuityForwardRegistrySql(v12Rows, CLAUDE_CONTINUITY_FORWARD_DB_CATALOG_BASELINE);
  const v12Runtime = predecessorArtifacts?.runtime ?? renderRuntimeProjection(v12Rows, {
    version: REGISTRY_V12_VERSION,
    dbCatalogBaseline: CLAUDE_CONTINUITY_FORWARD_DB_CATALOG_BASELINE,
  });
  for (const [path, source] of [
    [v12MigrationPath, v12Migration], [v12RuntimePath, v12Runtime],
  ]) {
    const observed = sha256(source);
    if (observed !== HISTORICAL_REGISTRY_ARTIFACT_SHA256[path])
      throw new Error(`sealed historical SCAC v12 artifact changed: ${path}: ${observed}`);
  }

  const headerMarker =
    "-- SCAC-11: forward-only mutation registry v12 after Claude continuity activation.";
  const coreStart = v12Migration.indexOf(headerMarker);
  if (coreStart < 0 || v12Migration.indexOf(headerMarker, coreStart + headerMarker.length) >= 0)
    throw new Error("sealed SCAC v12 migration has no exact successor core boundary");
  const v12Core = v12Migration.slice(coreStart);
  const currentV12Marker = "create or replace function ops.scac_mutation_catalog_v12_current()";
  const policyMarker =
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v11;";
  const currentV12Start = v12Core.indexOf(currentV12Marker);
  const secondCurrentV12 = v12Core.indexOf(
    currentV12Marker, currentV12Start + currentV12Marker.length);
  const v11HistoryMarker =
    "alter function ops.scac_mutation_catalog_v11_current() rename to scac_mutation_catalog_v11_live_at_seal;";
  const v11HistoryStart = v12Core.indexOf(v11HistoryMarker);
  const secondV11History = v12Core.indexOf(
    v11HistoryMarker, v11HistoryStart + v11HistoryMarker.length);
  const policyStart = v12Core.indexOf(policyMarker);
  const secondPolicy = v12Core.indexOf(policyMarker, policyStart + policyMarker.length);
  if (v11HistoryStart < 0 || secondV11History >= 0 || currentV12Start <= v11HistoryStart ||
      secondCurrentV12 >= 0 || policyStart <= currentV12Start || secondPolicy >= 0)
    throw new Error("sealed SCAC v12 migration has no exact catalog successor boundary");
  const installedV11History = v12Core.slice(v11HistoryStart, currentV12Start);
  const v12Current = v12Core.slice(currentV12Start, policyStart);
  const v12History =
`alter function ops.scac_mutation_catalog_v12_current() rename to scac_mutation_catalog_v12_live_at_seal;
create or replace function ops.scac_mutation_registry_v12_seal_available()
returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$
  select ops.scac_mutation_registry_seal_valid('scac-mutation-registry.v12')
$fn$;
create or replace function ops.scac_mutation_catalog_v12_current()
returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$
  select ops.scac_mutation_catalog_v12_live_at_seal()
$fn$;
comment on function ops.scac_mutation_registry_v12_seal_available() is 'Exact immutable v12 registry seal; separate from whether the live catalog still equals v12.';
comment on function ops.scac_mutation_catalog_v12_current() is 'Historical v12 live-catalog validator; expected to become false after the v13 authority surface is installed.';

`;
  const renderV13Current = baseline => {
    let current = v12Current
      .replaceAll("scac_mutation_catalog_v12_current", "scac_mutation_catalog_v13_current")
      .replaceAll("scac-mutation-registry.v12", "scac-mutation-registry.v13");
    current = replaceExactlyOnce(current,
      `if observed_count<>${CLAUDE_CONTINUITY_FORWARD_DB_CATALOG_BASELINE.secdef_execute.count} or observed_digest<>'${CLAUDE_CONTINUITY_FORWARD_DB_CATALOG_BASELINE.secdef_execute.digest}' then return false; end if;`,
      `if observed_count<>${baseline.secdef_execute.count} or observed_digest<>'${baseline.secdef_execute.digest}' then return false; end if;`,
      "Claude startup v13 security-definer baseline");
    current = replaceExactlyOnce(current,
      `if observed_count<>${CLAUDE_CONTINUITY_FORWARD_DB_CATALOG_BASELINE.relation_dml.count} or observed_digest<>'${CLAUDE_CONTINUITY_FORWARD_DB_CATALOG_BASELINE.relation_dml.digest}' then return false; end if;`,
      `if observed_count<>${baseline.relation_dml.count} or observed_digest<>'${baseline.relation_dml.digest}' then return false; end if;`,
      "Claude startup v13 relation baseline");
    current = replaceExactlyOnce(current,
      `if observed_count<>${CLAUDE_CONTINUITY_FORWARD_DB_CATALOG_BASELINE.column_dml.count} or observed_digest<>'${CLAUDE_CONTINUITY_FORWARD_DB_CATALOG_BASELINE.column_dml.digest}' then return false; end if;`,
      `if observed_count<>${baseline.column_dml.count} or observed_digest<>'${baseline.column_dml.digest}' then return false; end if;`,
      "Claude startup v13 column baseline");
    return replaceExactlyOnce(current,
      `return observed_count=${CLAUDE_CONTINUITY_FORWARD_DB_CATALOG_BASELINE.role_authority.count} and observed_digest='${CLAUDE_CONTINUITY_FORWARD_DB_CATALOG_BASELINE.role_authority.digest}';`,
      `return observed_count=${baseline.role_authority.count} and observed_digest='${baseline.role_authority.digest}';`,
      "Claude startup v13 role-authority baseline");
  };
  const v13Current = renderV13Current(dbCatalogBaseline);

  let sql = replaceExactlyOnce(v12Core, v12Current,
    "__CLAUDE_STARTUP_V12_CATALOG_SUCCESSOR__", "Claude startup v12 current catalog block");
  sql = replaceExactlyOnce(sql, installedV11History, "",
    "Claude startup already-installed v11 catalog history");
  sql = replaceExactlyOnce(sql, headerMarker,
    "-- SCAC-12: forward-only mutation registry v13 after Claude startup activation.",
    "Claude startup migration header");
  sql = sql
    .replaceAll("scac-mutation-registry.v12", "scac-mutation-registry.v13")
    .replaceAll("_v12", "_v13")
    .replaceAll(" v12", " v13");
  sql = replaceExactlyOnce(sql, JSON.stringify(CLAUDE_CONTINUITY_FORWARD_DB_CATALOG_BASELINE),
    JSON.stringify(dbCatalogBaseline), "Claude startup v13 catalog projection");
  sql = replaceExactlyOnce(sql,
    `'${v12Seal.digest}',${v12Seal.entryCount},${v12Seal.sourceEntryCount},`,
    `'sha256:${v13Digest}',${entryCount},${rows.length},`,
    "Claude startup v13 registry row");
  sql = replaceExactlyOnce(sql,
    `ops.scac_mutation_registration_v13('${v12Seal.digest}',`,
    `ops.scac_mutation_registration_v13('sha256:${v13Digest}',`,
    "Claude startup v13 snapshot registry lookup");
  sql = replaceExactlyOnce(sql,
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v11;",
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v12;",
    "Claude startup policy snapshot predecessor");
  sql = replaceExactlyOnce(sql, "__CLAUDE_STARTUP_V12_CATALOG_SUCCESSOR__",
    `${v12History}${v13Current}`, "Claude startup v12 catalog history insertion");

  const v12CatalogJson = JSON.stringify(CLAUDE_CONTINUITY_FORWARD_DB_CATALOG_BASELINE);
  sql = replaceExactlyOnce(sql,
    "check (registry_version in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v4','scac-mutation-registry.v5','scac-mutation-registry.v6','scac-mutation-registry.v7','scac-mutation-registry.v8','scac-mutation-registry.v9','scac-mutation-registry.v10','scac-mutation-registry.v11','scac-mutation-registry.v13'))",
    "check (registry_version in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v4','scac-mutation-registry.v5','scac-mutation-registry.v6','scac-mutation-registry.v7','scac-mutation-registry.v8','scac-mutation-registry.v9','scac-mutation-registry.v10','scac-mutation-registry.v11','scac-mutation-registry.v12','scac-mutation-registry.v13'))",
    "Claude startup registry-version constraint");
  sql = replaceExactlyOnce(sql,
    "if p_registry_version not in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v4','scac-mutation-registry.v5','scac-mutation-registry.v6','scac-mutation-registry.v7','scac-mutation-registry.v8','scac-mutation-registry.v9','scac-mutation-registry.v10','scac-mutation-registry.v11') then return false; end if;",
    "if p_registry_version not in ('scac-mutation-registry.v1','scac-mutation-registry.v2','scac-mutation-registry.v3','scac-mutation-registry.v4','scac-mutation-registry.v5','scac-mutation-registry.v6','scac-mutation-registry.v7','scac-mutation-registry.v8','scac-mutation-registry.v9','scac-mutation-registry.v10','scac-mutation-registry.v11','scac-mutation-registry.v12') then return false; end if;",
    "Claude startup historical seal allowlist");
  sql = replaceExactlyOnce(sql,
    `    when 'scac-mutation-registry.v11' then '${HISTORICAL_REGISTRY_SEALS.v11.digest}' end;`,
    `    when 'scac-mutation-registry.v11' then '${HISTORICAL_REGISTRY_SEALS.v11.digest}'\n    when '${v12Seal.version}' then '${v12Seal.digest}' end;`,
    "Claude startup historical digest case");
  sql = replaceExactlyOnce(sql,
    `    when 'scac-mutation-registry.v11' then '${JSON.stringify(CODEX_CONTINUITY_FORWARD_DB_CATALOG_BASELINE)}'::jsonb end;`,
    `    when 'scac-mutation-registry.v11' then '${JSON.stringify(CODEX_CONTINUITY_FORWARD_DB_CATALOG_BASELINE)}'::jsonb\n    when '${v12Seal.version}' then '${v12CatalogJson}'::jsonb end;`,
    "Claude startup historical catalog case");
  sql = replaceExactlyOnce(sql,
    `    ('scac-mutation-registry.v11','${HISTORICAL_REGISTRY_SEALS.v11.digest}',${HISTORICAL_REGISTRY_SEALS.v11.entryCount},${HISTORICAL_REGISTRY_SEALS.v11.sourceEntryCount})\n`,
    `    ('scac-mutation-registry.v11','${HISTORICAL_REGISTRY_SEALS.v11.digest}',${HISTORICAL_REGISTRY_SEALS.v11.entryCount},${HISTORICAL_REGISTRY_SEALS.v11.sourceEntryCount}),\n    ('${v12Seal.version}','${v12Seal.digest}',${v12Seal.entryCount},${v12Seal.sourceEntryCount})\n`,
    "Claude startup historical seal tuple");
  sql = replaceExactlyOnce(sql,
    "    ops.scac_mutation_registry_v11_seal_available()) then",
    "    ops.scac_mutation_registry_v11_seal_available() and\n    ops.scac_mutation_registry_v12_seal_available()) then",
    "Claude startup snapshot predecessor seal");
  sql = replaceExactlyOnce(sql,
    `or (r.registry_version='scac-mutation-registry.v13' and r.registry_digest='${v12Seal.digest}')`,
    `or (r.registry_version='scac-mutation-registry.v12' and r.registry_digest='${v12Seal.digest}')\n         or (r.registry_version='scac-mutation-registry.v13' and r.registry_digest='sha256:${v13Digest}')`,
    "Claude startup epoch-chain digest cases");
  sql = replaceExactlyOnce(sql,
    `  (registry_version='scac-mutation-registry.v13' and registry_digest='${v12Seal.digest}')`,
    `  (registry_version='scac-mutation-registry.v12' and registry_digest='${v12Seal.digest}') or\n  (registry_version='scac-mutation-registry.v13' and registry_digest='sha256:${v13Digest}')`,
    "Claude startup epoch constraint digest cases");
  sql = replaceExactlyOnce(sql,
    `'{registry_digest}',to_jsonb('${v12Seal.digest}'::text)`,
    `'{registry_digest}',to_jsonb('sha256:${v13Digest}'::text)`,
    "Claude startup snapshot registry digest");
  sql = replaceExactlyOnce(sql,
    "ops.scac_mutation_registry_v11_seal_available(),ops.scac_mutation_catalog_v13_current()",
    "ops.scac_mutation_registry_v11_seal_available(),ops.scac_mutation_catalog_v12_live_at_seal(),ops.scac_mutation_catalog_v12_current(),ops.scac_mutation_registry_v12_seal_available(),ops.scac_mutation_catalog_v13_current()",
    "Claude startup historical function revoke list");
  sql = replaceExactlyOnce(sql,
    "Claude continuity successor snapshot: current policy epochs bind mutation registry v13 while historical v2/v3/v4/v5/v6/v7/v8/v9/v10/v11 epochs remain immutable.",
    "Claude startup successor snapshot: current policy epochs bind mutation registry v13 while historical v2/v3/v4/v5/v6/v7/v8/v9/v10/v11/v12 epochs remain immutable.",
    "Claude startup policy snapshot comment");
  sql = replaceExactlyOnce(sql,
    `(select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v13')<>${v12Seal.entryCount}`,
    `(select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v13')<>${entryCount}`,
    "Claude startup v13 entry count guard");
  sql = replaceExactlyOnce(sql,
    `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v11')<>'${HISTORICAL_REGISTRY_SEALS.v11.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v11')<>${HISTORICAL_REGISTRY_SEALS.v11.entryCount} then raise exception 'sealed SCAC mutation registry v11 changed during successor creation'; end if;`,
    `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v12')<>'${v12Seal.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v12')<>${v12Seal.entryCount} then raise exception 'sealed SCAC mutation registry v12 changed during successor creation'; end if;`,
    "Claude startup predecessor seal guard");
  sql = replaceExactlyOnce(sql,
    "ops.scac_policy_epoch_snapshot(),ops.scac_policy_epoch_snapshot_v6(),ops.scac_policy_epoch_snapshot_v7(),ops.scac_policy_epoch_snapshot_v8(),ops.scac_policy_epoch_snapshot_v9(),ops.scac_policy_epoch_snapshot_v10(),ops.scac_policy_epoch_snapshot_v11(),",
    "ops.scac_policy_epoch_snapshot(),ops.scac_policy_epoch_snapshot_v6(),ops.scac_policy_epoch_snapshot_v7(),ops.scac_policy_epoch_snapshot_v8(),ops.scac_policy_epoch_snapshot_v9(),ops.scac_policy_epoch_snapshot_v10(),ops.scac_policy_epoch_snapshot_v11(),ops.scac_policy_epoch_snapshot_v12(),",
    "Claude startup historical policy snapshot revoke list");

  const seedStartMarker = "with seed as (select value as contract from jsonb_array_elements(";
  const seedEndMarker = "::jsonb))\ninsert into ops.scac_mutation_registry_entry";
  const seedStart = sql.indexOf(seedStartMarker);
  const secondSeedStart = sql.indexOf(seedStartMarker, seedStart + seedStartMarker.length);
  const seedEnd = sql.indexOf(seedEndMarker, seedStart + seedStartMarker.length);
  const secondSeedEnd = sql.indexOf(seedEndMarker, seedEnd + seedEndMarker.length);
  if (seedStart < 0 || secondSeedStart >= 0 || seedEnd < 0 || secondSeedEnd >= 0)
    throw new Error("sealed SCAC v12 migration has no exact source-seed boundary");
  const seed = JSON.stringify(rows.map(row => ({ ...row, entry_digest: `sha256:${sha256(row)}` })));
  sql = `${sql.slice(0, seedStart + seedStartMarker.length)}${sqlLiteral(seed)}${sql.slice(seedEnd)}`;
  sql = replaceExactlyOnce(sql,
    `(grant_snapshot->>'entry_count')::integer=${CLAUDE_CONTINUITY_FORWARD_DB_CATALOG_BASELINE.runtime_dml_grants.count} and\n    grant_snapshot->>'grant_digest'='${CLAUDE_CONTINUITY_FORWARD_DB_CATALOG_BASELINE.runtime_dml_grants.digest}'`,
    `(grant_snapshot->>'entry_count')::integer=${dbCatalogBaseline.runtime_dml_grants.count} and\n    grant_snapshot->>'grant_digest'='${dbCatalogBaseline.runtime_dml_grants.digest}'`,
    "Claude startup exact runtime grant binding");

  const preflightCurrent = renderV13Current(CLAUDE_STARTUP_PRE_V13_DB_CATALOG_BASELINE);
  const preflightBegin = preflightCurrent.indexOf("begin\n");
  const preflightEnd = preflightCurrent.lastIndexOf("end $fn$;");
  if (preflightBegin < 0 || preflightEnd <= preflightBegin)
    throw new Error("generated v13 catalog predicate has no exact preflight body boundary");
  let preflightBody = preflightCurrent.slice(preflightBegin + "begin\n".length, preflightEnd);
  preflightBody = preflightBody.replaceAll(
    "then return false; end if;",
    "then raise exception 'Claude startup pre-v13 catalog receipt drifted'; end if;");
  preflightBody = replaceExactlyOnce(preflightBody,
    `return observed_count=${CLAUDE_STARTUP_PRE_V13_DB_CATALOG_BASELINE.role_authority.count} and observed_digest='${CLAUDE_STARTUP_PRE_V13_DB_CATALOG_BASELINE.role_authority.digest}';`,
    `if observed_count<>${CLAUDE_STARTUP_PRE_V13_DB_CATALOG_BASELINE.role_authority.count} or observed_digest<>'${CLAUDE_STARTUP_PRE_V13_DB_CATALOG_BASELINE.role_authority.digest}' then raise exception 'Claude startup pre-v13 role-authority receipt drifted'; end if;`,
    "Claude startup pre-v13 role receipt");
  const predecessorHash = sha256(v12Migration);
  const predecessorPreflight =
`-- Exact disposable-PG17 post-0486 receipt. Refuse before creating any v13 function.
do $claude_startup_preflight$
declare observed_count integer; observed_digest text; grant_snapshot jsonb;
begin
  if (select count(*) from public.schema_migrations where filename='0486_claude_continuity_registry_activation.sql')<>1
     or not exists(select 1 from public.schema_migrations where filename='0486_claude_continuity_registry_activation.sql'
       and sha256='${predecessorHash}') then
    raise exception 'Claude startup pre-v13 migration ledger receipt drifted';
  end if;
  grant_snapshot:=ops.scac_runtime_dml_grant_snapshot();
  if (grant_snapshot->>'entry_count')::integer<>${CLAUDE_STARTUP_PRE_V13_DB_CATALOG_BASELINE.runtime_dml_grants.count}
     or grant_snapshot->>'grant_digest'<>'${CLAUDE_STARTUP_PRE_V13_DB_CATALOG_BASELINE.runtime_dml_grants.digest}' then
    raise exception 'Claude startup pre-v13 runtime grant receipt drifted';
  end if;
${preflightBody}end $claude_startup_preflight$;

`;
  return `${predecessorPreflight}${sql}`.replace(/\n+$/, "\n");
}

export function renderClaudeActorHydrationForwardRegistrySql(rows = fullInventory(),
  dbCatalogBaseline = CLAUDE_ACTOR_HYDRATION_FORWARD_DB_CATALOG_BASELINE,
  predecessorArtifacts = undefined) {
  const { v13: v13Seal } = HISTORICAL_REGISTRY_SEALS;
  const v14Digest = registryDigestFor(REGISTRY_V14_VERSION, rows, dbCatalogBaseline);
  const catalogCount = dbCatalogBaseline.secdef_execute.count +
    dbCatalogBaseline.relation_dml.count + dbCatalogBaseline.column_dml.count;
  const entryCount = rows.length + catalogCount;
  const v13MigrationPath = "migrations/0487_claude_startup_registry_activation.sql";
  const v13RuntimePath = "mcp-server/src/scac-mutation-registry.v13.generated.js";
  const v13Rows = frozenInventory(REGISTRY_V13_VERSION);
  const v13Migration = predecessorArtifacts?.migration ??
    renderClaudeStartupForwardRegistrySql(v13Rows, CLAUDE_STARTUP_FORWARD_DB_CATALOG_BASELINE);
  const v13Runtime = predecessorArtifacts?.runtime ?? renderRuntimeProjection(v13Rows, {
    version: REGISTRY_V13_VERSION,
    dbCatalogBaseline: CLAUDE_STARTUP_FORWARD_DB_CATALOG_BASELINE,
  });
  for (const [path, source] of [
    [v13MigrationPath, v13Migration], [v13RuntimePath, v13Runtime],
  ]) {
    const observed = sha256(source);
    if (observed !== HISTORICAL_REGISTRY_ARTIFACT_SHA256[path])
      throw new Error(`sealed historical SCAC v13 artifact changed: ${path}: ${observed}`);
  }

  const headerMarker =
    "-- SCAC-12: forward-only mutation registry v13 after Claude startup activation.";
  const coreStart = v13Migration.indexOf(headerMarker);
  if (coreStart < 0 || v13Migration.indexOf(headerMarker, coreStart + headerMarker.length) >= 0)
    throw new Error("sealed SCAC v13 migration has no exact successor core boundary");
  const v13Core = v13Migration.slice(coreStart);
  const currentV13Marker = "create or replace function ops.scac_mutation_catalog_v13_current()";
  const policyMarker =
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v12;";
  const currentV13Start = v13Core.indexOf(currentV13Marker);
  const secondCurrentV13 = v13Core.indexOf(
    currentV13Marker, currentV13Start + currentV13Marker.length);
  const v12HistoryMarker =
    "alter function ops.scac_mutation_catalog_v12_current() rename to scac_mutation_catalog_v12_live_at_seal;";
  const v12HistoryStart = v13Core.indexOf(v12HistoryMarker);
  const secondV12History = v13Core.indexOf(
    v12HistoryMarker, v12HistoryStart + v12HistoryMarker.length);
  const policyStart = v13Core.indexOf(policyMarker);
  const secondPolicy = v13Core.indexOf(policyMarker, policyStart + policyMarker.length);
  if (v12HistoryStart < 0 || secondV12History >= 0 || currentV13Start <= v12HistoryStart ||
      secondCurrentV13 >= 0 || policyStart <= currentV13Start || secondPolicy >= 0)
    throw new Error("sealed SCAC v13 migration has no exact catalog successor boundary");
  const installedV12History = v13Core.slice(v12HistoryStart, currentV13Start);
  const v13Current = v13Core.slice(currentV13Start, policyStart);
  const v13History =
`alter function ops.scac_mutation_catalog_v13_current() rename to scac_mutation_catalog_v13_live_at_seal;
create or replace function ops.scac_mutation_registry_v13_seal_available()
returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$
  select ops.scac_mutation_registry_seal_valid('scac-mutation-registry.v13')
$fn$;
create or replace function ops.scac_mutation_catalog_v13_current()
returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$
  select ops.scac_mutation_catalog_v13_live_at_seal()
$fn$;
comment on function ops.scac_mutation_registry_v13_seal_available() is 'Exact immutable v13 registry seal; separate from whether the live catalog still equals v13.';
comment on function ops.scac_mutation_catalog_v13_current() is 'Historical v13 live-catalog validator; expected to become false after the v14 authority surface is installed.';

`;
  const renderV14Current = baseline => {
    let current = v13Current
      .replaceAll("scac_mutation_catalog_v13_current", "scac_mutation_catalog_v14_current")
      .replaceAll("scac-mutation-registry.v13", "scac-mutation-registry.v14");
    current = replaceExactlyOnce(current,
      `if observed_count<>${CLAUDE_STARTUP_FORWARD_DB_CATALOG_BASELINE.secdef_execute.count} or observed_digest<>'${CLAUDE_STARTUP_FORWARD_DB_CATALOG_BASELINE.secdef_execute.digest}' then return false; end if;`,
      `if observed_count<>${baseline.secdef_execute.count} or observed_digest<>'${baseline.secdef_execute.digest}' then return false; end if;`,
      "Claude actor hydration v14 security-definer baseline");
    current = replaceExactlyOnce(current,
      `if observed_count<>${CLAUDE_STARTUP_FORWARD_DB_CATALOG_BASELINE.relation_dml.count} or observed_digest<>'${CLAUDE_STARTUP_FORWARD_DB_CATALOG_BASELINE.relation_dml.digest}' then return false; end if;`,
      `if observed_count<>${baseline.relation_dml.count} or observed_digest<>'${baseline.relation_dml.digest}' then return false; end if;`,
      "Claude actor hydration v14 relation baseline");
    current = replaceExactlyOnce(current,
      `if observed_count<>${CLAUDE_STARTUP_FORWARD_DB_CATALOG_BASELINE.column_dml.count} or observed_digest<>'${CLAUDE_STARTUP_FORWARD_DB_CATALOG_BASELINE.column_dml.digest}' then return false; end if;`,
      `if observed_count<>${baseline.column_dml.count} or observed_digest<>'${baseline.column_dml.digest}' then return false; end if;`,
      "Claude actor hydration v14 column baseline");
    return replaceExactlyOnce(current,
      `return observed_count=${CLAUDE_STARTUP_FORWARD_DB_CATALOG_BASELINE.role_authority.count} and observed_digest='${CLAUDE_STARTUP_FORWARD_DB_CATALOG_BASELINE.role_authority.digest}';`,
      `return observed_count=${baseline.role_authority.count} and observed_digest='${baseline.role_authority.digest}';`,
      "Claude actor hydration v14 role-authority baseline");
  };
  const v14Current = renderV14Current(dbCatalogBaseline);

  let sql = replaceExactlyOnce(v13Core, v13Current,
    "__CLAUDE_ACTOR_HYDRATION_V13_CATALOG_SUCCESSOR__", "Claude actor hydration v13 current catalog block");
  sql = replaceExactlyOnce(sql, installedV12History, "",
    "Claude actor hydration already-installed v12 catalog history");
  sql = replaceExactlyOnce(sql, headerMarker,
    "-- SCAC-12: forward-only mutation registry v14 after Claude recovery actor hydration.",
    "Claude actor hydration migration header");
  sql = sql
    .replaceAll("scac-mutation-registry.v13", "scac-mutation-registry.v14")
    .replaceAll("_v13", "_v14")
    .replaceAll(" v13", " v14");
  sql = replaceExactlyOnce(sql, JSON.stringify(CLAUDE_STARTUP_FORWARD_DB_CATALOG_BASELINE),
    JSON.stringify(dbCatalogBaseline), "Claude actor hydration v14 catalog projection");
  sql = replaceExactlyOnce(sql,
    `'${v13Seal.digest}',${v13Seal.entryCount},${v13Seal.sourceEntryCount},`,
    `'sha256:${v14Digest}',${entryCount},${rows.length},`,
    "Claude actor hydration v14 registry row");
  sql = replaceExactlyOnce(sql,
    `ops.scac_mutation_registration_v14('${v13Seal.digest}',`,
    `ops.scac_mutation_registration_v14('sha256:${v14Digest}',`,
    "Claude actor hydration v14 snapshot registry lookup");
  sql = replaceExactlyOnce(sql,
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v12;",
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v13;",
    "Claude actor hydration policy snapshot predecessor");
  sql = replaceExactlyOnce(sql, "__CLAUDE_ACTOR_HYDRATION_V13_CATALOG_SUCCESSOR__",
    `${v13History}${v14Current}`, "Claude actor hydration v13 catalog history insertion");

  const versionsThrough13 = Array.from({ length: 13 }, (_, index) =>
    `'scac-mutation-registry.v${index + 1}'`).join(",");
  const versionsThrough12 = Array.from({ length: 12 }, (_, index) =>
    `'scac-mutation-registry.v${index + 1}'`).join(",");
  sql = replaceExactlyOnce(sql,
    `check (registry_version in (${versionsThrough12},'scac-mutation-registry.v14'))`,
    `check (registry_version in (${versionsThrough13},'scac-mutation-registry.v14'))`,
    "Claude actor hydration registry-version constraint");
  sql = replaceExactlyOnce(sql,
    `if p_registry_version not in (${versionsThrough12}) then return false; end if;`,
    `if p_registry_version not in (${versionsThrough13}) then return false; end if;`,
    "Claude actor hydration historical seal allowlist");
  sql = replaceExactlyOnce(sql,
    `    when 'scac-mutation-registry.v12' then '${HISTORICAL_REGISTRY_SEALS.v12.digest}' end;`,
    `    when 'scac-mutation-registry.v12' then '${HISTORICAL_REGISTRY_SEALS.v12.digest}'\n    when '${v13Seal.version}' then '${v13Seal.digest}' end;`,
    "Claude actor hydration historical digest case");
  sql = replaceExactlyOnce(sql,
    `    when 'scac-mutation-registry.v12' then '${JSON.stringify(CLAUDE_CONTINUITY_FORWARD_DB_CATALOG_BASELINE)}'::jsonb end;`,
    `    when 'scac-mutation-registry.v12' then '${JSON.stringify(CLAUDE_CONTINUITY_FORWARD_DB_CATALOG_BASELINE)}'::jsonb\n    when '${v13Seal.version}' then '${JSON.stringify(CLAUDE_STARTUP_FORWARD_DB_CATALOG_BASELINE)}'::jsonb end;`,
    "Claude actor hydration historical catalog case");
  sql = replaceExactlyOnce(sql,
    `    ('scac-mutation-registry.v12','${HISTORICAL_REGISTRY_SEALS.v12.digest}',${HISTORICAL_REGISTRY_SEALS.v12.entryCount},${HISTORICAL_REGISTRY_SEALS.v12.sourceEntryCount})\n`,
    `    ('scac-mutation-registry.v12','${HISTORICAL_REGISTRY_SEALS.v12.digest}',${HISTORICAL_REGISTRY_SEALS.v12.entryCount},${HISTORICAL_REGISTRY_SEALS.v12.sourceEntryCount}),\n    ('${v13Seal.version}','${v13Seal.digest}',${v13Seal.entryCount},${v13Seal.sourceEntryCount})\n`,
    "Claude actor hydration historical seal tuple");
  sql = replaceExactlyOnce(sql,
    "    ops.scac_mutation_registry_v12_seal_available()) then",
    "    ops.scac_mutation_registry_v12_seal_available() and\n    ops.scac_mutation_registry_v13_seal_available()) then",
    "Claude actor hydration snapshot predecessor seal");
  sql = replaceExactlyOnce(sql,
    `or (r.registry_version='scac-mutation-registry.v14' and r.registry_digest='${v13Seal.digest}')`,
    `or (r.registry_version='scac-mutation-registry.v13' and r.registry_digest='${v13Seal.digest}')\n         or (r.registry_version='scac-mutation-registry.v14' and r.registry_digest='sha256:${v14Digest}')`,
    "Claude actor hydration epoch-chain digest cases");
  sql = replaceExactlyOnce(sql,
    `  (registry_version='scac-mutation-registry.v14' and registry_digest='${v13Seal.digest}')`,
    `  (registry_version='scac-mutation-registry.v13' and registry_digest='${v13Seal.digest}') or\n  (registry_version='scac-mutation-registry.v14' and registry_digest='sha256:${v14Digest}')`,
    "Claude actor hydration epoch constraint digest cases");
  sql = replaceExactlyOnce(sql,
    `'{registry_digest}',to_jsonb('${v13Seal.digest}'::text)`,
    `'{registry_digest}',to_jsonb('sha256:${v14Digest}'::text)`,
    "Claude actor hydration snapshot registry digest");
  sql = replaceExactlyOnce(sql,
    "ops.scac_mutation_registry_v12_seal_available(),ops.scac_mutation_catalog_v14_current()",
    "ops.scac_mutation_registry_v12_seal_available(),ops.scac_mutation_catalog_v13_live_at_seal(),ops.scac_mutation_catalog_v13_current(),ops.scac_mutation_registry_v13_seal_available(),ops.scac_mutation_catalog_v14_current()",
    "Claude actor hydration historical function revoke list");
  sql = replaceExactlyOnce(sql,
    "Claude startup successor snapshot: current policy epochs bind mutation registry v14 while historical v2/v3/v4/v5/v6/v7/v8/v9/v10/v11/v12 epochs remain immutable.",
    "Claude recovery successor snapshot: current policy epochs bind mutation registry v14 while historical v2/v3/v4/v5/v6/v7/v8/v9/v10/v11/v12/v13 epochs remain immutable.",
    "Claude actor hydration policy snapshot comment");
  sql = replaceExactlyOnce(sql,
    `(select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v14')<>${v13Seal.entryCount}`,
    `(select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v14')<>${entryCount}`,
    "Claude actor hydration v14 entry count guard");
  sql = replaceExactlyOnce(sql,
    `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v12')<>'${HISTORICAL_REGISTRY_SEALS.v12.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v12')<>${HISTORICAL_REGISTRY_SEALS.v12.entryCount} then raise exception 'sealed SCAC mutation registry v12 changed during successor creation'; end if;`,
    `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v13')<>'${v13Seal.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v13')<>${v13Seal.entryCount} then raise exception 'sealed SCAC mutation registry v13 changed during successor creation'; end if;`,
    "Claude actor hydration predecessor seal guard");
  sql = replaceExactlyOnce(sql,
    "ops.scac_policy_epoch_snapshot(),ops.scac_policy_epoch_snapshot_v6(),ops.scac_policy_epoch_snapshot_v7(),ops.scac_policy_epoch_snapshot_v8(),ops.scac_policy_epoch_snapshot_v9(),ops.scac_policy_epoch_snapshot_v10(),ops.scac_policy_epoch_snapshot_v11(),ops.scac_policy_epoch_snapshot_v12(),",
    "ops.scac_policy_epoch_snapshot(),ops.scac_policy_epoch_snapshot_v6(),ops.scac_policy_epoch_snapshot_v7(),ops.scac_policy_epoch_snapshot_v8(),ops.scac_policy_epoch_snapshot_v9(),ops.scac_policy_epoch_snapshot_v10(),ops.scac_policy_epoch_snapshot_v11(),ops.scac_policy_epoch_snapshot_v12(),ops.scac_policy_epoch_snapshot_v13(),",
    "Claude actor hydration historical policy snapshot revoke list");

  const seedStartMarker = "with seed as (select value as contract from jsonb_array_elements(";
  const seedEndMarker = "::jsonb))\ninsert into ops.scac_mutation_registry_entry";
  const seedStart = sql.indexOf(seedStartMarker);
  const secondSeedStart = sql.indexOf(seedStartMarker, seedStart + seedStartMarker.length);
  const seedEnd = sql.indexOf(seedEndMarker, seedStart + seedStartMarker.length);
  const secondSeedEnd = sql.indexOf(seedEndMarker, seedEnd + seedEndMarker.length);
  if (seedStart < 0 || secondSeedStart >= 0 || seedEnd < 0 || secondSeedEnd >= 0)
    throw new Error("sealed SCAC v13 migration has no exact source-seed boundary");
  const seed = JSON.stringify(rows.map(row => ({ ...row, entry_digest: `sha256:${sha256(row)}` })));
  sql = `${sql.slice(0, seedStart + seedStartMarker.length)}${sqlLiteral(seed)}${sql.slice(seedEnd)}`;

  const preflightCurrent = renderV14Current(CLAUDE_ACTOR_HYDRATION_PRE_V14_DB_CATALOG_BASELINE);
  const preflightBegin = preflightCurrent.indexOf("begin\n");
  const preflightEnd = preflightCurrent.lastIndexOf("end $fn$;");
  if (preflightBegin < 0 || preflightEnd <= preflightBegin)
    throw new Error("generated v14 catalog predicate has no exact preflight body boundary");
  let preflightBody = preflightCurrent.slice(preflightBegin + "begin\n".length, preflightEnd);
  preflightBody = preflightBody.replaceAll(
    "then return false; end if;",
    "then raise exception 'Claude actor hydration pre-v14 catalog receipt drifted'; end if;");
  preflightBody = replaceExactlyOnce(preflightBody,
    `return observed_count=${CLAUDE_ACTOR_HYDRATION_PRE_V14_DB_CATALOG_BASELINE.role_authority.count} and observed_digest='${CLAUDE_ACTOR_HYDRATION_PRE_V14_DB_CATALOG_BASELINE.role_authority.digest}';`,
    `if observed_count<>${CLAUDE_ACTOR_HYDRATION_PRE_V14_DB_CATALOG_BASELINE.role_authority.count} or observed_digest<>'${CLAUDE_ACTOR_HYDRATION_PRE_V14_DB_CATALOG_BASELINE.role_authority.digest}' then raise exception 'Claude actor hydration pre-v14 role-authority receipt drifted'; end if;`,
    "Claude actor hydration pre-v14 role receipt");
  const predecessorHash = sha256(v13Migration);
  const predecessorPreflight =
`-- Exact disposable-PG17 post-0487 receipt. Refuse before creating any v14 function.
do $claude_actor_hydration_preflight$
declare observed_count integer; observed_digest text; grant_snapshot jsonb;
begin
  if (select count(*) from public.schema_migrations where filename='0487_claude_startup_registry_activation.sql')<>1
     or not exists(select 1 from public.schema_migrations where filename='0487_claude_startup_registry_activation.sql'
       and sha256='${predecessorHash}') then
    raise exception 'Claude actor hydration pre-v14 migration ledger receipt drifted';
  end if;
  grant_snapshot:=ops.scac_runtime_dml_grant_snapshot();
  if (grant_snapshot->>'entry_count')::integer<>${CLAUDE_ACTOR_HYDRATION_PRE_V14_DB_CATALOG_BASELINE.runtime_dml_grants.count}
     or grant_snapshot->>'grant_digest'<>'${CLAUDE_ACTOR_HYDRATION_PRE_V14_DB_CATALOG_BASELINE.runtime_dml_grants.digest}' then
    raise exception 'Claude actor hydration pre-v14 runtime grant receipt drifted';
  end if;
${preflightBody}end $claude_actor_hydration_preflight$;

`;
  return `${predecessorPreflight}${sql}`.replace(/\n+$/, "\n");
}


export function renderClaudeConfigPreservationForwardRegistrySql(rows = fullInventory(),
  dbCatalogBaseline = CLAUDE_CONFIG_PRESERVATION_FORWARD_DB_CATALOG_BASELINE,
  predecessorArtifacts = undefined) {
  const { v14: v14Seal } = HISTORICAL_REGISTRY_SEALS;
  const v15Digest = registryDigestFor(REGISTRY_V15_VERSION, rows, dbCatalogBaseline);
  const catalogCount = dbCatalogBaseline.secdef_execute.count +
    dbCatalogBaseline.relation_dml.count + dbCatalogBaseline.column_dml.count;
  const entryCount = rows.length + catalogCount;
  const v14MigrationPath = "migrations/0488_claude_actor_hydration_registry_activation.sql";
  const v14RuntimePath = "mcp-server/src/scac-mutation-registry.v14.generated.js";
  const v14Rows = frozenInventory(REGISTRY_V14_VERSION);
  const v14Migration = predecessorArtifacts?.migration ??
    renderClaudeActorHydrationForwardRegistrySql(v14Rows, CLAUDE_ACTOR_HYDRATION_FORWARD_DB_CATALOG_BASELINE);
  const v14Runtime = predecessorArtifacts?.runtime ?? renderRuntimeProjection(v14Rows, {
    version: REGISTRY_V14_VERSION,
    dbCatalogBaseline: CLAUDE_ACTOR_HYDRATION_FORWARD_DB_CATALOG_BASELINE,
  });
  for (const [path, source] of [
    [v14MigrationPath, v14Migration], [v14RuntimePath, v14Runtime],
  ]) {
    const observed = sha256(source);
    if (observed !== HISTORICAL_REGISTRY_ARTIFACT_SHA256[path])
      throw new Error(`sealed historical SCAC v14 artifact changed: ${path}: ${observed}`);
  }

  const headerMarker =
    "-- SCAC-12: forward-only mutation registry v14 after Claude recovery actor hydration.";
  const coreStart = v14Migration.indexOf(headerMarker);
  if (coreStart < 0 || v14Migration.indexOf(headerMarker, coreStart + headerMarker.length) >= 0)
    throw new Error("sealed SCAC v14 migration has no exact successor core boundary");
  const v14Core = v14Migration.slice(coreStart);
  const currentV14Marker = "create or replace function ops.scac_mutation_catalog_v14_current()";
  const policyMarker =
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v13;";
  const currentV14Start = v14Core.indexOf(currentV14Marker);
  const secondCurrentV14 = v14Core.indexOf(
    currentV14Marker, currentV14Start + currentV14Marker.length);
  const v13HistoryMarker =
    "alter function ops.scac_mutation_catalog_v13_current() rename to scac_mutation_catalog_v13_live_at_seal;";
  const v13HistoryStart = v14Core.indexOf(v13HistoryMarker);
  const secondV13History = v14Core.indexOf(
    v13HistoryMarker, v13HistoryStart + v13HistoryMarker.length);
  const policyStart = v14Core.indexOf(policyMarker);
  const secondPolicy = v14Core.indexOf(policyMarker, policyStart + policyMarker.length);
  if (v13HistoryStart < 0 || secondV13History >= 0 || currentV14Start <= v13HistoryStart ||
      secondCurrentV14 >= 0 || policyStart <= currentV14Start || secondPolicy >= 0)
    throw new Error("sealed SCAC v14 migration has no exact catalog successor boundary");
  const installedV13History = v14Core.slice(v13HistoryStart, currentV14Start);
  const v14Current = v14Core.slice(currentV14Start, policyStart);
  const v14History =
`alter function ops.scac_mutation_catalog_v14_current() rename to scac_mutation_catalog_v14_live_at_seal;
create or replace function ops.scac_mutation_registry_v14_seal_available()
returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$
  select ops.scac_mutation_registry_seal_valid('scac-mutation-registry.v14')
$fn$;
create or replace function ops.scac_mutation_catalog_v14_current()
returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$
  select ops.scac_mutation_catalog_v14_live_at_seal()
$fn$;
comment on function ops.scac_mutation_registry_v14_seal_available() is 'Exact immutable v14 registry seal; separate from whether the live catalog still equals v14.';
comment on function ops.scac_mutation_catalog_v14_current() is 'Historical v14 live-catalog validator; expected to become false after the v15 authority surface is installed.';

`;
  const renderV15Current = baseline => {
    let current = v14Current
      .replaceAll("scac_mutation_catalog_v14_current", "scac_mutation_catalog_v15_current")
      .replaceAll("scac-mutation-registry.v14", "scac-mutation-registry.v15");
    current = replaceExactlyOnce(current,
      `if observed_count<>${CLAUDE_ACTOR_HYDRATION_FORWARD_DB_CATALOG_BASELINE.secdef_execute.count} or observed_digest<>'${CLAUDE_ACTOR_HYDRATION_FORWARD_DB_CATALOG_BASELINE.secdef_execute.digest}' then return false; end if;`,
      `if observed_count<>${baseline.secdef_execute.count} or observed_digest<>'${baseline.secdef_execute.digest}' then return false; end if;`,
      "Claude config preservation v15 security-definer baseline");
    current = replaceExactlyOnce(current,
      `if observed_count<>${CLAUDE_ACTOR_HYDRATION_FORWARD_DB_CATALOG_BASELINE.relation_dml.count} or observed_digest<>'${CLAUDE_ACTOR_HYDRATION_FORWARD_DB_CATALOG_BASELINE.relation_dml.digest}' then return false; end if;`,
      `if observed_count<>${baseline.relation_dml.count} or observed_digest<>'${baseline.relation_dml.digest}' then return false; end if;`,
      "Claude config preservation v15 relation baseline");
    current = replaceExactlyOnce(current,
      `if observed_count<>${CLAUDE_ACTOR_HYDRATION_FORWARD_DB_CATALOG_BASELINE.column_dml.count} or observed_digest<>'${CLAUDE_ACTOR_HYDRATION_FORWARD_DB_CATALOG_BASELINE.column_dml.digest}' then return false; end if;`,
      `if observed_count<>${baseline.column_dml.count} or observed_digest<>'${baseline.column_dml.digest}' then return false; end if;`,
      "Claude config preservation v15 column baseline");
    return replaceExactlyOnce(current,
      `return observed_count=${CLAUDE_ACTOR_HYDRATION_FORWARD_DB_CATALOG_BASELINE.role_authority.count} and observed_digest='${CLAUDE_ACTOR_HYDRATION_FORWARD_DB_CATALOG_BASELINE.role_authority.digest}';`,
      `return observed_count=${baseline.role_authority.count} and observed_digest='${baseline.role_authority.digest}';`,
      "Claude config preservation v15 role-authority baseline");
  };
  const v15Current = renderV15Current(dbCatalogBaseline);

  let sql = replaceExactlyOnce(v14Core, v14Current,
    "__CLAUDE_ACTOR_HYDRATION_V14_CATALOG_SUCCESSOR__", "Claude config preservation v14 current catalog block");
  sql = replaceExactlyOnce(sql, installedV13History, "",
    "Claude config preservation already-installed v13 catalog history");
  sql = replaceExactlyOnce(sql, headerMarker,
    "-- SCAC-12: forward-only mutation registry v15 after Claude continuity config preservation.",
    "Claude config preservation migration header");
  sql = sql
    .replaceAll("scac-mutation-registry.v14", "scac-mutation-registry.v15")
    .replaceAll("_v14", "_v15")
    .replaceAll(" v14", " v15");
  sql = replaceExactlyOnce(sql, JSON.stringify(CLAUDE_ACTOR_HYDRATION_FORWARD_DB_CATALOG_BASELINE),
    JSON.stringify(dbCatalogBaseline), "Claude config preservation v15 catalog projection");
  sql = replaceExactlyOnce(sql,
    `'${v14Seal.digest}',${v14Seal.entryCount},${v14Seal.sourceEntryCount},`,
    `'sha256:${v15Digest}',${entryCount},${rows.length},`,
    "Claude config preservation v15 registry row");
  sql = replaceExactlyOnce(sql,
    `ops.scac_mutation_registration_v15('${v14Seal.digest}',`,
    `ops.scac_mutation_registration_v15('sha256:${v15Digest}',`,
    "Claude config preservation v15 snapshot registry lookup");
  sql = replaceExactlyOnce(sql,
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v13;",
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v14;",
    "Claude config preservation policy snapshot predecessor");
  sql = replaceExactlyOnce(sql, "__CLAUDE_ACTOR_HYDRATION_V14_CATALOG_SUCCESSOR__",
    `${v14History}${v15Current}`, "Claude config preservation v14 catalog history insertion");

  const versionsThrough14 = Array.from({ length: 14 }, (_, index) =>
    `'scac-mutation-registry.v${index + 1}'`).join(",");
  const versionsThrough13 = Array.from({ length: 13 }, (_, index) =>
    `'scac-mutation-registry.v${index + 1}'`).join(",");
  sql = replaceExactlyOnce(sql,
    `check (registry_version in (${versionsThrough13},'scac-mutation-registry.v15'))`,
    `check (registry_version in (${versionsThrough14},'scac-mutation-registry.v15'))`,
    "Claude config preservation registry-version constraint");
  sql = replaceExactlyOnce(sql,
    `if p_registry_version not in (${versionsThrough13}) then return false; end if;`,
    `if p_registry_version not in (${versionsThrough14}) then return false; end if;`,
    "Claude config preservation historical seal allowlist");
  sql = replaceExactlyOnce(sql,
    `    when 'scac-mutation-registry.v13' then '${HISTORICAL_REGISTRY_SEALS.v13.digest}' end;`,
    `    when 'scac-mutation-registry.v13' then '${HISTORICAL_REGISTRY_SEALS.v13.digest}'\n    when '${v14Seal.version}' then '${v14Seal.digest}' end;`,
    "Claude config preservation historical digest case");
  sql = replaceExactlyOnce(sql,
    `    when 'scac-mutation-registry.v13' then '${JSON.stringify(CLAUDE_STARTUP_FORWARD_DB_CATALOG_BASELINE)}'::jsonb end;`,
    `    when 'scac-mutation-registry.v13' then '${JSON.stringify(CLAUDE_STARTUP_FORWARD_DB_CATALOG_BASELINE)}'::jsonb\n    when '${v14Seal.version}' then '${JSON.stringify(CLAUDE_ACTOR_HYDRATION_FORWARD_DB_CATALOG_BASELINE)}'::jsonb end;`,
    "Claude config preservation historical catalog case");
  sql = replaceExactlyOnce(sql,
    `    ('scac-mutation-registry.v13','${HISTORICAL_REGISTRY_SEALS.v13.digest}',${HISTORICAL_REGISTRY_SEALS.v13.entryCount},${HISTORICAL_REGISTRY_SEALS.v13.sourceEntryCount})\n`,
    `    ('scac-mutation-registry.v13','${HISTORICAL_REGISTRY_SEALS.v13.digest}',${HISTORICAL_REGISTRY_SEALS.v13.entryCount},${HISTORICAL_REGISTRY_SEALS.v13.sourceEntryCount}),\n    ('${v14Seal.version}','${v14Seal.digest}',${v14Seal.entryCount},${v14Seal.sourceEntryCount})\n`,
    "Claude config preservation historical seal tuple");
  sql = replaceExactlyOnce(sql,
    "    ops.scac_mutation_registry_v13_seal_available()) then",
    "    ops.scac_mutation_registry_v13_seal_available() and\n    ops.scac_mutation_registry_v14_seal_available()) then",
    "Claude config preservation snapshot predecessor seal");
  sql = replaceExactlyOnce(sql,
    `or (r.registry_version='scac-mutation-registry.v15' and r.registry_digest='${v14Seal.digest}')`,
    `or (r.registry_version='scac-mutation-registry.v14' and r.registry_digest='${v14Seal.digest}')\n         or (r.registry_version='scac-mutation-registry.v15' and r.registry_digest='sha256:${v15Digest}')`,
    "Claude config preservation epoch-chain digest cases");
  sql = replaceExactlyOnce(sql,
    `  (registry_version='scac-mutation-registry.v15' and registry_digest='${v14Seal.digest}')`,
    `  (registry_version='scac-mutation-registry.v14' and registry_digest='${v14Seal.digest}') or\n  (registry_version='scac-mutation-registry.v15' and registry_digest='sha256:${v15Digest}')`,
    "Claude config preservation epoch constraint digest cases");
  sql = replaceExactlyOnce(sql,
    `'{registry_digest}',to_jsonb('${v14Seal.digest}'::text)`,
    `'{registry_digest}',to_jsonb('sha256:${v15Digest}'::text)`,
    "Claude config preservation snapshot registry digest");
  sql = replaceExactlyOnce(sql,
    "ops.scac_mutation_registry_v13_seal_available(),ops.scac_mutation_catalog_v15_current()",
    "ops.scac_mutation_registry_v13_seal_available(),ops.scac_mutation_catalog_v14_live_at_seal(),ops.scac_mutation_catalog_v14_current(),ops.scac_mutation_registry_v14_seal_available(),ops.scac_mutation_catalog_v15_current()",
    "Claude config preservation historical function revoke list");
  sql = replaceExactlyOnce(sql,
    "Claude recovery successor snapshot: current policy epochs bind mutation registry v15 while historical v2/v3/v4/v5/v6/v7/v8/v9/v10/v11/v12/v13 epochs remain immutable.",
    "Claude config-preservation successor snapshot: current policy epochs bind mutation registry v15 while historical v2/v3/v4/v5/v6/v7/v8/v9/v10/v11/v12/v13/v14 epochs remain immutable.",
    "Claude config preservation policy snapshot comment");
  sql = replaceExactlyOnce(sql,
    `(select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v15')<>${v14Seal.entryCount}`,
    `(select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v15')<>${entryCount}`,
    "Claude config preservation v15 entry count guard");
  sql = replaceExactlyOnce(sql,
    `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v13')<>'${HISTORICAL_REGISTRY_SEALS.v13.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v13')<>${HISTORICAL_REGISTRY_SEALS.v13.entryCount} then raise exception 'sealed SCAC mutation registry v13 changed during successor creation'; end if;`,
    `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v14')<>'${v14Seal.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v14')<>${v14Seal.entryCount} then raise exception 'sealed SCAC mutation registry v14 changed during successor creation'; end if;`,
    "Claude config preservation predecessor seal guard");
  sql = replaceExactlyOnce(sql,
    "ops.scac_policy_epoch_snapshot(),ops.scac_policy_epoch_snapshot_v6(),ops.scac_policy_epoch_snapshot_v7(),ops.scac_policy_epoch_snapshot_v8(),ops.scac_policy_epoch_snapshot_v9(),ops.scac_policy_epoch_snapshot_v10(),ops.scac_policy_epoch_snapshot_v11(),ops.scac_policy_epoch_snapshot_v12(),ops.scac_policy_epoch_snapshot_v13(),",
    "ops.scac_policy_epoch_snapshot(),ops.scac_policy_epoch_snapshot_v6(),ops.scac_policy_epoch_snapshot_v7(),ops.scac_policy_epoch_snapshot_v8(),ops.scac_policy_epoch_snapshot_v9(),ops.scac_policy_epoch_snapshot_v10(),ops.scac_policy_epoch_snapshot_v11(),ops.scac_policy_epoch_snapshot_v12(),ops.scac_policy_epoch_snapshot_v13(),ops.scac_policy_epoch_snapshot_v14(),",
    "Claude config preservation historical policy snapshot revoke list");

  const seedStartMarker = "with seed as (select value as contract from jsonb_array_elements(";
  const seedEndMarker = "::jsonb))\ninsert into ops.scac_mutation_registry_entry";
  const seedStart = sql.indexOf(seedStartMarker);
  const secondSeedStart = sql.indexOf(seedStartMarker, seedStart + seedStartMarker.length);
  const seedEnd = sql.indexOf(seedEndMarker, seedStart + seedStartMarker.length);
  const secondSeedEnd = sql.indexOf(seedEndMarker, seedEnd + seedEndMarker.length);
  if (seedStart < 0 || secondSeedStart >= 0 || seedEnd < 0 || secondSeedEnd >= 0)
    throw new Error("sealed SCAC v14 migration has no exact source-seed boundary");
  const seed = JSON.stringify(rows.map(row => ({ ...row, entry_digest: `sha256:${sha256(row)}` })));
  sql = `${sql.slice(0, seedStart + seedStartMarker.length)}${sqlLiteral(seed)}${sql.slice(seedEnd)}`;

  const preflightCurrent = renderV15Current(CLAUDE_CONFIG_PRESERVATION_PRE_V15_DB_CATALOG_BASELINE);
  const preflightBegin = preflightCurrent.indexOf("begin\n");
  const preflightEnd = preflightCurrent.lastIndexOf("end $fn$;");
  if (preflightBegin < 0 || preflightEnd <= preflightBegin)
    throw new Error("generated v15 catalog predicate has no exact preflight body boundary");
  let preflightBody = preflightCurrent.slice(preflightBegin + "begin\n".length, preflightEnd);
  preflightBody = preflightBody.replaceAll(
    "then return false; end if;",
    "then raise exception 'Claude config preservation pre-v15 catalog receipt drifted'; end if;");
  preflightBody = replaceExactlyOnce(preflightBody,
    `return observed_count=${CLAUDE_CONFIG_PRESERVATION_PRE_V15_DB_CATALOG_BASELINE.role_authority.count} and observed_digest='${CLAUDE_CONFIG_PRESERVATION_PRE_V15_DB_CATALOG_BASELINE.role_authority.digest}';`,
    `if observed_count<>${CLAUDE_CONFIG_PRESERVATION_PRE_V15_DB_CATALOG_BASELINE.role_authority.count} or observed_digest<>'${CLAUDE_CONFIG_PRESERVATION_PRE_V15_DB_CATALOG_BASELINE.role_authority.digest}' then raise exception 'Claude config preservation pre-v15 role-authority receipt drifted'; end if;`,
    "Claude config preservation pre-v15 role receipt");
  const predecessorHash = sha256(v14Migration);
  const predecessorPreflight =
`-- Exact disposable-PG17 post-0488 receipt. Refuse before creating any v15 function.
do $claude_config_preservation_preflight$
declare observed_count integer; observed_digest text; grant_snapshot jsonb;
begin
  if (select count(*) from public.schema_migrations where filename='0488_claude_actor_hydration_registry_activation.sql')<>1
     or not exists(select 1 from public.schema_migrations where filename='0488_claude_actor_hydration_registry_activation.sql'
       and sha256='${predecessorHash}') then
    raise exception 'Claude config preservation pre-v15 migration ledger receipt drifted';
  end if;
  grant_snapshot:=ops.scac_runtime_dml_grant_snapshot();
  if (grant_snapshot->>'entry_count')::integer<>${CLAUDE_CONFIG_PRESERVATION_PRE_V15_DB_CATALOG_BASELINE.runtime_dml_grants.count}
     or grant_snapshot->>'grant_digest'<>'${CLAUDE_CONFIG_PRESERVATION_PRE_V15_DB_CATALOG_BASELINE.runtime_dml_grants.digest}' then
    raise exception 'Claude config preservation pre-v15 runtime grant receipt drifted';
  end if;
${preflightBody}end $claude_config_preservation_preflight$;

`;
  return `${predecessorPreflight}${sql}`.replace(/\n+$/, "\n");
}

export function renderCodexCompactionCheckpointForwardRegistrySql(rows = fullInventory(),
  dbCatalogBaseline = CODEX_COMPACTION_CHECKPOINT_FORWARD_DB_CATALOG_BASELINE,
  predecessorArtifacts = undefined) {
  const { v15: v15Seal } = HISTORICAL_REGISTRY_SEALS;
  const v16Digest = registryDigestFor(REGISTRY_V16_VERSION, rows, dbCatalogBaseline);
  const catalogCount = dbCatalogBaseline.secdef_execute.count +
    dbCatalogBaseline.relation_dml.count + dbCatalogBaseline.column_dml.count;
  const entryCount = rows.length + catalogCount;
  const v15MigrationPath = "migrations/0489_claude_config_preservation_registry_activation.sql";
  const v15RuntimePath = "mcp-server/src/scac-mutation-registry.v15.generated.js";
  const v15Rows = frozenInventory(REGISTRY_V15_VERSION);
  const v15Migration = predecessorArtifacts?.migration ??
    renderClaudeConfigPreservationForwardRegistrySql(
      v15Rows, CLAUDE_CONFIG_PRESERVATION_FORWARD_DB_CATALOG_BASELINE);
  const v15Runtime = predecessorArtifacts?.runtime ?? renderRuntimeProjection(v15Rows, {
    version: REGISTRY_V15_VERSION,
    dbCatalogBaseline: CLAUDE_CONFIG_PRESERVATION_FORWARD_DB_CATALOG_BASELINE,
  });
  for (const [path, source] of [
    [v15MigrationPath, v15Migration], [v15RuntimePath, v15Runtime],
  ]) {
    const observed = sha256(source);
    if (observed !== HISTORICAL_REGISTRY_ARTIFACT_SHA256[path])
      throw new Error(`sealed historical SCAC v15 artifact changed: ${path}: ${observed}`);
  }

  const headerMarker =
    "-- SCAC-12: forward-only mutation registry v15 after Claude continuity config preservation.";
  const coreStart = v15Migration.indexOf(headerMarker);
  if (coreStart < 0 || v15Migration.indexOf(headerMarker, coreStart + headerMarker.length) >= 0)
    throw new Error("sealed SCAC v15 migration has no exact successor core boundary");
  const v15Core = v15Migration.slice(coreStart);
  const currentV15Marker = "create or replace function ops.scac_mutation_catalog_v15_current()";
  const policyMarker =
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v14;";
  const currentV15Start = v15Core.indexOf(currentV15Marker);
  const secondCurrentV15 = v15Core.indexOf(
    currentV15Marker, currentV15Start + currentV15Marker.length);
  const v14HistoryMarker =
    "alter function ops.scac_mutation_catalog_v14_current() rename to scac_mutation_catalog_v14_live_at_seal;";
  const v14HistoryStart = v15Core.indexOf(v14HistoryMarker);
  const secondV14History = v15Core.indexOf(
    v14HistoryMarker, v14HistoryStart + v14HistoryMarker.length);
  const policyStart = v15Core.indexOf(policyMarker);
  const secondPolicy = v15Core.indexOf(policyMarker, policyStart + policyMarker.length);
  if (v14HistoryStart < 0 || secondV14History >= 0 || currentV15Start <= v14HistoryStart ||
      secondCurrentV15 >= 0 || policyStart <= currentV15Start || secondPolicy >= 0)
    throw new Error("sealed SCAC v15 migration has no exact catalog successor boundary");
  const installedV14History = v15Core.slice(v14HistoryStart, currentV15Start);
  const v15Current = v15Core.slice(currentV15Start, policyStart);
  const v15History =
`alter function ops.scac_mutation_catalog_v15_current() rename to scac_mutation_catalog_v15_live_at_seal;
create or replace function ops.scac_mutation_registry_v15_seal_available()
returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$
  select ops.scac_mutation_registry_seal_valid('scac-mutation-registry.v15')
$fn$;
create or replace function ops.scac_mutation_catalog_v15_current()
returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$
  select ops.scac_mutation_catalog_v15_live_at_seal()
$fn$;
comment on function ops.scac_mutation_registry_v15_seal_available() is 'Exact immutable v15 registry seal; separate from whether the live catalog still equals v15.';
comment on function ops.scac_mutation_catalog_v15_current() is 'Historical v15 live-catalog validator; expected to become false after the v16 authority surface is installed.';

`;
  const renderV16Current = baseline => {
    let current = v15Current
      .replaceAll("scac_mutation_catalog_v15_current", "scac_mutation_catalog_v16_current")
      .replaceAll("scac-mutation-registry.v15", "scac-mutation-registry.v16");
    current = replaceExactlyOnce(current,
      `if observed_count<>${CLAUDE_CONFIG_PRESERVATION_FORWARD_DB_CATALOG_BASELINE.secdef_execute.count} or observed_digest<>'${CLAUDE_CONFIG_PRESERVATION_FORWARD_DB_CATALOG_BASELINE.secdef_execute.digest}' then return false; end if;`,
      `if observed_count<>${baseline.secdef_execute.count} or observed_digest<>'${baseline.secdef_execute.digest}' then return false; end if;`,
      "Codex compaction checkpoint v16 security-definer baseline");
    current = replaceExactlyOnce(current,
      `if observed_count<>${CLAUDE_CONFIG_PRESERVATION_FORWARD_DB_CATALOG_BASELINE.relation_dml.count} or observed_digest<>'${CLAUDE_CONFIG_PRESERVATION_FORWARD_DB_CATALOG_BASELINE.relation_dml.digest}' then return false; end if;`,
      `if observed_count<>${baseline.relation_dml.count} or observed_digest<>'${baseline.relation_dml.digest}' then return false; end if;`,
      "Codex compaction checkpoint v16 relation baseline");
    current = replaceExactlyOnce(current,
      `if observed_count<>${CLAUDE_CONFIG_PRESERVATION_FORWARD_DB_CATALOG_BASELINE.column_dml.count} or observed_digest<>'${CLAUDE_CONFIG_PRESERVATION_FORWARD_DB_CATALOG_BASELINE.column_dml.digest}' then return false; end if;`,
      `if observed_count<>${baseline.column_dml.count} or observed_digest<>'${baseline.column_dml.digest}' then return false; end if;`,
      "Codex compaction checkpoint v16 column baseline");
    return replaceExactlyOnce(current,
      `return observed_count=${CLAUDE_CONFIG_PRESERVATION_FORWARD_DB_CATALOG_BASELINE.role_authority.count} and observed_digest='${CLAUDE_CONFIG_PRESERVATION_FORWARD_DB_CATALOG_BASELINE.role_authority.digest}';`,
      `return observed_count=${baseline.role_authority.count} and observed_digest='${baseline.role_authority.digest}';`,
      "Codex compaction checkpoint v16 role-authority baseline");
  };
  const v16Current = renderV16Current(dbCatalogBaseline);

  let sql = replaceExactlyOnce(v15Core, v15Current,
    "__CLAUDE_CONFIG_PRESERVATION_V15_CATALOG_SUCCESSOR__",
    "Codex compaction checkpoint v15 current catalog block");
  sql = replaceExactlyOnce(sql, installedV14History, "",
    "Codex compaction checkpoint already-installed v14 catalog history");
  sql = replaceExactlyOnce(sql, headerMarker,
    "-- SCAC-12: forward-only mutation registry v16 after Codex compaction checkpoint refresh.",
    "Codex compaction checkpoint migration header");
  sql = sql
    .replaceAll("scac-mutation-registry.v15", "scac-mutation-registry.v16")
    .replaceAll("_v15", "_v16")
    .replaceAll(" v15", " v16");
  sql = replaceExactlyOnce(sql, JSON.stringify(CLAUDE_CONFIG_PRESERVATION_FORWARD_DB_CATALOG_BASELINE),
    JSON.stringify(dbCatalogBaseline), "Codex compaction checkpoint v16 catalog projection");
  sql = replaceExactlyOnce(sql,
    `'${v15Seal.digest}',${v15Seal.entryCount},${v15Seal.sourceEntryCount},`,
    `'sha256:${v16Digest}',${entryCount},${rows.length},`,
    "Codex compaction checkpoint v16 registry row");
  sql = replaceExactlyOnce(sql,
    `ops.scac_mutation_registration_v16('${v15Seal.digest}',`,
    `ops.scac_mutation_registration_v16('sha256:${v16Digest}',`,
    "Codex compaction checkpoint v16 snapshot registry lookup");
  sql = replaceExactlyOnce(sql,
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v14;",
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v15;",
    "Codex compaction checkpoint policy snapshot predecessor");
  sql = replaceExactlyOnce(sql, "__CLAUDE_CONFIG_PRESERVATION_V15_CATALOG_SUCCESSOR__",
    `${v15History}${v16Current}`, "Codex compaction checkpoint v15 catalog history insertion");

  const versionsThrough15 = Array.from({ length: 15 }, (_, index) =>
    `'scac-mutation-registry.v${index + 1}'`).join(",");
  const versionsThrough14 = Array.from({ length: 14 }, (_, index) =>
    `'scac-mutation-registry.v${index + 1}'`).join(",");
  sql = replaceExactlyOnce(sql,
    `check (registry_version in (${versionsThrough14},'scac-mutation-registry.v16'))`,
    `check (registry_version in (${versionsThrough15},'scac-mutation-registry.v16'))`,
    "Codex compaction checkpoint registry-version constraint");
  sql = replaceExactlyOnce(sql,
    `if p_registry_version not in (${versionsThrough14}) then return false; end if;`,
    `if p_registry_version not in (${versionsThrough15}) then return false; end if;`,
    "Codex compaction checkpoint historical seal allowlist");
  sql = replaceExactlyOnce(sql,
    `    when 'scac-mutation-registry.v14' then '${HISTORICAL_REGISTRY_SEALS.v14.digest}' end;`,
    `    when 'scac-mutation-registry.v14' then '${HISTORICAL_REGISTRY_SEALS.v14.digest}'\n    when '${v15Seal.version}' then '${v15Seal.digest}' end;`,
    "Codex compaction checkpoint historical digest case");
  sql = replaceExactlyOnce(sql,
    `    when 'scac-mutation-registry.v14' then '${JSON.stringify(CLAUDE_ACTOR_HYDRATION_FORWARD_DB_CATALOG_BASELINE)}'::jsonb end;`,
    `    when 'scac-mutation-registry.v14' then '${JSON.stringify(CLAUDE_ACTOR_HYDRATION_FORWARD_DB_CATALOG_BASELINE)}'::jsonb\n    when '${v15Seal.version}' then '${JSON.stringify(CLAUDE_CONFIG_PRESERVATION_FORWARD_DB_CATALOG_BASELINE)}'::jsonb end;`,
    "Codex compaction checkpoint historical catalog case");
  sql = replaceExactlyOnce(sql,
    `    ('scac-mutation-registry.v14','${HISTORICAL_REGISTRY_SEALS.v14.digest}',${HISTORICAL_REGISTRY_SEALS.v14.entryCount},${HISTORICAL_REGISTRY_SEALS.v14.sourceEntryCount})\n`,
    `    ('scac-mutation-registry.v14','${HISTORICAL_REGISTRY_SEALS.v14.digest}',${HISTORICAL_REGISTRY_SEALS.v14.entryCount},${HISTORICAL_REGISTRY_SEALS.v14.sourceEntryCount}),\n    ('${v15Seal.version}','${v15Seal.digest}',${v15Seal.entryCount},${v15Seal.sourceEntryCount})\n`,
    "Codex compaction checkpoint historical seal tuple");
  sql = replaceExactlyOnce(sql,
    "    ops.scac_mutation_registry_v14_seal_available()) then",
    "    ops.scac_mutation_registry_v14_seal_available() and\n    ops.scac_mutation_registry_v15_seal_available()) then",
    "Codex compaction checkpoint snapshot predecessor seal");
  sql = replaceExactlyOnce(sql,
    `or (r.registry_version='scac-mutation-registry.v16' and r.registry_digest='${v15Seal.digest}')`,
    `or (r.registry_version='scac-mutation-registry.v15' and r.registry_digest='${v15Seal.digest}')\n         or (r.registry_version='scac-mutation-registry.v16' and r.registry_digest='sha256:${v16Digest}')`,
    "Codex compaction checkpoint epoch-chain digest cases");
  sql = replaceExactlyOnce(sql,
    `  (registry_version='scac-mutation-registry.v16' and registry_digest='${v15Seal.digest}')`,
    `  (registry_version='scac-mutation-registry.v15' and registry_digest='${v15Seal.digest}') or\n  (registry_version='scac-mutation-registry.v16' and registry_digest='sha256:${v16Digest}')`,
    "Codex compaction checkpoint epoch constraint digest cases");
  sql = replaceExactlyOnce(sql,
    `'{registry_digest}',to_jsonb('${v15Seal.digest}'::text)`,
    `'{registry_digest}',to_jsonb('sha256:${v16Digest}'::text)`,
    "Codex compaction checkpoint snapshot registry digest");
  sql = replaceExactlyOnce(sql,
    "ops.scac_mutation_registry_v14_seal_available(),ops.scac_mutation_catalog_v16_current()",
    "ops.scac_mutation_registry_v14_seal_available(),ops.scac_mutation_catalog_v15_live_at_seal(),ops.scac_mutation_catalog_v15_current(),ops.scac_mutation_registry_v15_seal_available(),ops.scac_mutation_catalog_v16_current()",
    "Codex compaction checkpoint historical function revoke list");
  sql = replaceExactlyOnce(sql,
    "Claude config-preservation successor snapshot: current policy epochs bind mutation registry v16 while historical v2/v3/v4/v5/v6/v7/v8/v9/v10/v11/v12/v13/v14 epochs remain immutable.",
    "Codex compaction checkpoint successor snapshot: current policy epochs bind mutation registry v16 while historical v2/v3/v4/v5/v6/v7/v8/v9/v10/v11/v12/v13/v14/v15 epochs remain immutable.",
    "Codex compaction checkpoint policy snapshot comment");
  sql = replaceExactlyOnce(sql,
    `(select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v16')<>${v15Seal.entryCount}`,
    `(select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v16')<>${entryCount}`,
    "Codex compaction checkpoint v16 entry count guard");
  sql = replaceExactlyOnce(sql,
    `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v14')<>'${HISTORICAL_REGISTRY_SEALS.v14.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v14')<>${HISTORICAL_REGISTRY_SEALS.v14.entryCount} then raise exception 'sealed SCAC mutation registry v14 changed during successor creation'; end if;`,
    `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v15')<>'${v15Seal.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v15')<>${v15Seal.entryCount} then raise exception 'sealed SCAC mutation registry v15 changed during successor creation'; end if;`,
    "Codex compaction checkpoint predecessor seal guard");
  sql = replaceExactlyOnce(sql,
    "ops.scac_policy_epoch_snapshot(),ops.scac_policy_epoch_snapshot_v6(),ops.scac_policy_epoch_snapshot_v7(),ops.scac_policy_epoch_snapshot_v8(),ops.scac_policy_epoch_snapshot_v9(),ops.scac_policy_epoch_snapshot_v10(),ops.scac_policy_epoch_snapshot_v11(),ops.scac_policy_epoch_snapshot_v12(),ops.scac_policy_epoch_snapshot_v13(),ops.scac_policy_epoch_snapshot_v14(),",
    "ops.scac_policy_epoch_snapshot(),ops.scac_policy_epoch_snapshot_v6(),ops.scac_policy_epoch_snapshot_v7(),ops.scac_policy_epoch_snapshot_v8(),ops.scac_policy_epoch_snapshot_v9(),ops.scac_policy_epoch_snapshot_v10(),ops.scac_policy_epoch_snapshot_v11(),ops.scac_policy_epoch_snapshot_v12(),ops.scac_policy_epoch_snapshot_v13(),ops.scac_policy_epoch_snapshot_v14(),ops.scac_policy_epoch_snapshot_v15(),",
    "Codex compaction checkpoint historical policy snapshot revoke list");

  const seedStartMarker = "with seed as (select value as contract from jsonb_array_elements(";
  const seedEndMarker = "::jsonb))\ninsert into ops.scac_mutation_registry_entry";
  const seedStart = sql.indexOf(seedStartMarker);
  const secondSeedStart = sql.indexOf(seedStartMarker, seedStart + seedStartMarker.length);
  const seedEnd = sql.indexOf(seedEndMarker, seedStart + seedStartMarker.length);
  const secondSeedEnd = sql.indexOf(seedEndMarker, seedEnd + seedEndMarker.length);
  if (seedStart < 0 || secondSeedStart >= 0 || seedEnd < 0 || secondSeedEnd >= 0)
    throw new Error("sealed SCAC v15 migration has no exact source-seed boundary");
  const seed = JSON.stringify(rows.map(row => ({ ...row, entry_digest: `sha256:${sha256(row)}` })));
  sql = `${sql.slice(0, seedStart + seedStartMarker.length)}${sqlLiteral(seed)}${sql.slice(seedEnd)}`;

  const preflightCurrent = renderV16Current(CODEX_COMPACTION_CHECKPOINT_PRE_V16_DB_CATALOG_BASELINE);
  const preflightBegin = preflightCurrent.indexOf("begin\n");
  const preflightEnd = preflightCurrent.lastIndexOf("end $fn$;");
  if (preflightBegin < 0 || preflightEnd <= preflightBegin)
    throw new Error("generated v16 catalog predicate has no exact preflight body boundary");
  let preflightBody = preflightCurrent.slice(preflightBegin + "begin\n".length, preflightEnd);
  preflightBody = preflightBody.replaceAll(
    "then return false; end if;",
    "then raise exception 'Codex compaction checkpoint pre-v16 catalog receipt drifted'; end if;");
  preflightBody = replaceExactlyOnce(preflightBody,
    `return observed_count=${CODEX_COMPACTION_CHECKPOINT_PRE_V16_DB_CATALOG_BASELINE.role_authority.count} and observed_digest='${CODEX_COMPACTION_CHECKPOINT_PRE_V16_DB_CATALOG_BASELINE.role_authority.digest}';`,
    `if observed_count<>${CODEX_COMPACTION_CHECKPOINT_PRE_V16_DB_CATALOG_BASELINE.role_authority.count} or observed_digest<>'${CODEX_COMPACTION_CHECKPOINT_PRE_V16_DB_CATALOG_BASELINE.role_authority.digest}' then raise exception 'Codex compaction checkpoint pre-v16 role-authority receipt drifted'; end if;`,
    "Codex compaction checkpoint pre-v16 role receipt");
  const predecessorHash = sha256(v15Migration);
  const predecessorPreflight =
`-- Exact disposable-Postgres post-0489 receipt. Refuse before creating any v16 function.
do $codex_compaction_checkpoint_preflight$
declare observed_count integer; observed_digest text; grant_snapshot jsonb;
begin
  if (select count(*) from public.schema_migrations where filename='0489_claude_config_preservation_registry_activation.sql')<>1
     or not exists(select 1 from public.schema_migrations where filename='0489_claude_config_preservation_registry_activation.sql'
       and sha256='${predecessorHash}') then
    raise exception 'Codex compaction checkpoint pre-v16 migration ledger receipt drifted';
  end if;
  grant_snapshot:=ops.scac_runtime_dml_grant_snapshot();
  if (grant_snapshot->>'entry_count')::integer<>${CODEX_COMPACTION_CHECKPOINT_PRE_V16_DB_CATALOG_BASELINE.runtime_dml_grants.count}
     or grant_snapshot->>'grant_digest'<>'${CODEX_COMPACTION_CHECKPOINT_PRE_V16_DB_CATALOG_BASELINE.runtime_dml_grants.digest}' then
    raise exception 'Codex compaction checkpoint pre-v16 runtime grant receipt drifted';
  end if;
${preflightBody}end $codex_compaction_checkpoint_preflight$;

`;
  return `${predecessorPreflight}${sql}`.replace(/\n+$/, "\n");
}


export function renderBackupGuardStatusForwardRegistrySql(rows = fullInventory(),
  dbCatalogBaseline = BACKUP_GUARD_STATUS_FORWARD_DB_CATALOG_BASELINE,
  predecessorArtifacts = undefined) {
  const { v16: v16Seal } = HISTORICAL_REGISTRY_SEALS;
  const v17Digest = registryDigestFor(REGISTRY_V17_VERSION, rows, dbCatalogBaseline);
  const catalogCount = dbCatalogBaseline.secdef_execute.count +
    dbCatalogBaseline.relation_dml.count + dbCatalogBaseline.column_dml.count;
  const entryCount = rows.length + catalogCount;
  const v16MigrationPath = "migrations/0490_codex_compaction_checkpoint_registry_activation.sql";
  const v16RuntimePath = "mcp-server/src/scac-mutation-registry.v16.generated.js";
  const v16Rows = frozenInventory(REGISTRY_V16_VERSION);
  const v16Migration = predecessorArtifacts?.migration ??
    renderCodexCompactionCheckpointForwardRegistrySql(
      v16Rows, CODEX_COMPACTION_CHECKPOINT_FORWARD_DB_CATALOG_BASELINE);
  const v16Runtime = predecessorArtifacts?.runtime ?? renderRuntimeProjection(v16Rows, {
    version: REGISTRY_V16_VERSION,
    dbCatalogBaseline: CODEX_COMPACTION_CHECKPOINT_FORWARD_DB_CATALOG_BASELINE,
  });
  for (const [path, source] of [
    [v16MigrationPath, v16Migration], [v16RuntimePath, v16Runtime],
  ]) {
    const observed = sha256(source);
    if (observed !== HISTORICAL_REGISTRY_ARTIFACT_SHA256[path])
      throw new Error(`sealed historical SCAC v16 artifact changed: ${path}: ${observed}`);
  }

  const headerMarker =
    "-- SCAC-12: forward-only mutation registry v16 after Codex compaction checkpoint refresh.";
  const coreStart = v16Migration.indexOf(headerMarker);
  if (coreStart < 0 || v16Migration.indexOf(headerMarker, coreStart + headerMarker.length) >= 0)
    throw new Error("sealed SCAC v16 migration has no exact successor core boundary");
  const v16Core = v16Migration.slice(coreStart);
  const currentV16Marker = "create or replace function ops.scac_mutation_catalog_v16_current()";
  const policyMarker =
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v15;";
  const currentV16Start = v16Core.indexOf(currentV16Marker);
  const secondCurrentV16 = v16Core.indexOf(
    currentV16Marker, currentV16Start + currentV16Marker.length);
  const v15HistoryMarker =
    "alter function ops.scac_mutation_catalog_v15_current() rename to scac_mutation_catalog_v15_live_at_seal;";
  const v15HistoryStart = v16Core.indexOf(v15HistoryMarker);
  const secondV15History = v16Core.indexOf(
    v15HistoryMarker, v15HistoryStart + v15HistoryMarker.length);
  const policyStart = v16Core.indexOf(policyMarker);
  const secondPolicy = v16Core.indexOf(policyMarker, policyStart + policyMarker.length);
  if (v15HistoryStart < 0 || secondV15History >= 0 || currentV16Start <= v15HistoryStart ||
      secondCurrentV16 >= 0 || policyStart <= currentV16Start || secondPolicy >= 0)
    throw new Error("sealed SCAC v16 migration has no exact catalog successor boundary");
  const installedV15History = v16Core.slice(v15HistoryStart, currentV16Start);
  const v16Current = v16Core.slice(currentV16Start, policyStart);
  const v16History =
`alter function ops.scac_mutation_catalog_v16_current() rename to scac_mutation_catalog_v16_live_at_seal;
create or replace function ops.scac_mutation_registry_v16_seal_available()
returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$
  select ops.scac_mutation_registry_seal_valid('scac-mutation-registry.v16')
$fn$;
create or replace function ops.scac_mutation_catalog_v16_current()
returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$
  select ops.scac_mutation_catalog_v16_live_at_seal()
$fn$;
comment on function ops.scac_mutation_registry_v16_seal_available() is 'Exact immutable v16 registry seal; separate from whether the live catalog still equals v16.';
comment on function ops.scac_mutation_catalog_v16_current() is 'Historical v16 live-catalog validator; expected to become false after the v17 authority surface is installed.';

`;
  const renderV17Current = baseline => {
    let current = v16Current
      .replaceAll("scac_mutation_catalog_v16_current", "scac_mutation_catalog_v17_current")
      .replaceAll("scac-mutation-registry.v16", "scac-mutation-registry.v17");
    current = replaceExactlyOnce(current,
      `if observed_count<>${CODEX_COMPACTION_CHECKPOINT_FORWARD_DB_CATALOG_BASELINE.secdef_execute.count} or observed_digest<>'${CODEX_COMPACTION_CHECKPOINT_FORWARD_DB_CATALOG_BASELINE.secdef_execute.digest}' then return false; end if;`,
      `if observed_count<>${baseline.secdef_execute.count} or observed_digest<>'${baseline.secdef_execute.digest}' then return false; end if;`,
      "Backup guard/status v17 security-definer baseline");
    current = replaceExactlyOnce(current,
      `if observed_count<>${CODEX_COMPACTION_CHECKPOINT_FORWARD_DB_CATALOG_BASELINE.relation_dml.count} or observed_digest<>'${CODEX_COMPACTION_CHECKPOINT_FORWARD_DB_CATALOG_BASELINE.relation_dml.digest}' then return false; end if;`,
      `if observed_count<>${baseline.relation_dml.count} or observed_digest<>'${baseline.relation_dml.digest}' then return false; end if;`,
      "Backup guard/status v17 relation baseline");
    current = replaceExactlyOnce(current,
      `if observed_count<>${CODEX_COMPACTION_CHECKPOINT_FORWARD_DB_CATALOG_BASELINE.column_dml.count} or observed_digest<>'${CODEX_COMPACTION_CHECKPOINT_FORWARD_DB_CATALOG_BASELINE.column_dml.digest}' then return false; end if;`,
      `if observed_count<>${baseline.column_dml.count} or observed_digest<>'${baseline.column_dml.digest}' then return false; end if;`,
      "Backup guard/status v17 column baseline");
    return replaceExactlyOnce(current,
      `return observed_count=${CODEX_COMPACTION_CHECKPOINT_FORWARD_DB_CATALOG_BASELINE.role_authority.count} and observed_digest='${CODEX_COMPACTION_CHECKPOINT_FORWARD_DB_CATALOG_BASELINE.role_authority.digest}';`,
      `return observed_count=${baseline.role_authority.count} and observed_digest='${baseline.role_authority.digest}';`,
      "Backup guard/status v17 role-authority baseline");
  };
  const v17Current = renderV17Current(dbCatalogBaseline);

  let sql = replaceExactlyOnce(v16Core, v16Current,
    "__CODEX_COMPACTION_CHECKPOINT_V16_CATALOG_SUCCESSOR__",
    "Backup guard/status v16 current catalog block");
  sql = replaceExactlyOnce(sql, installedV15History, "",
    "Backup guard/status already-installed v15 catalog history");
  sql = replaceExactlyOnce(sql, headerMarker,
    "-- SCAC-12: forward-only mutation registry v17 after backup guard/status repair.",
    "Backup guard/status migration header");
  sql = sql
    .replaceAll("scac-mutation-registry.v16", "scac-mutation-registry.v17")
    .replaceAll("_v16", "_v17")
    .replaceAll(" v16", " v17");
  sql = replaceExactlyOnce(sql, JSON.stringify(CODEX_COMPACTION_CHECKPOINT_FORWARD_DB_CATALOG_BASELINE),
    JSON.stringify(dbCatalogBaseline), "Backup guard/status v17 catalog projection");
  sql = replaceExactlyOnce(sql,
    `'${v16Seal.digest}',${v16Seal.entryCount},${v16Seal.sourceEntryCount},`,
    `'sha256:${v17Digest}',${entryCount},${rows.length},`,
    "Backup guard/status v17 registry row");
  sql = replaceExactlyOnce(sql,
    `ops.scac_mutation_registration_v17('${v16Seal.digest}',`,
    `ops.scac_mutation_registration_v17('sha256:${v17Digest}',`,
    "Backup guard/status v17 snapshot registry lookup");
  sql = replaceExactlyOnce(sql,
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v15;",
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v16;",
    "Backup guard/status policy snapshot predecessor");
  sql = replaceExactlyOnce(sql, "__CODEX_COMPACTION_CHECKPOINT_V16_CATALOG_SUCCESSOR__",
    `${v16History}${v17Current}`, "Backup guard/status v16 catalog history insertion");

  const versionsThrough16 = Array.from({ length: 16 }, (_, index) =>
    `'scac-mutation-registry.v${index + 1}'`).join(",");
  const versionsThrough15 = Array.from({ length: 15 }, (_, index) =>
    `'scac-mutation-registry.v${index + 1}'`).join(",");
  sql = replaceExactlyOnce(sql,
    `check (registry_version in (${versionsThrough15},'scac-mutation-registry.v17'))`,
    `check (registry_version in (${versionsThrough16},'scac-mutation-registry.v17'))`,
    "Backup guard/status registry-version constraint");
  sql = replaceExactlyOnce(sql,
    `if p_registry_version not in (${versionsThrough15}) then return false; end if;`,
    `if p_registry_version not in (${versionsThrough16}) then return false; end if;`,
    "Backup guard/status historical seal allowlist");
  sql = replaceExactlyOnce(sql,
    `    when 'scac-mutation-registry.v15' then '${HISTORICAL_REGISTRY_SEALS.v15.digest}' end;`,
    `    when 'scac-mutation-registry.v15' then '${HISTORICAL_REGISTRY_SEALS.v15.digest}'\n    when '${v16Seal.version}' then '${v16Seal.digest}' end;`,
    "Backup guard/status historical digest case");
  sql = replaceExactlyOnce(sql,
    `    when 'scac-mutation-registry.v15' then '${JSON.stringify(CLAUDE_CONFIG_PRESERVATION_FORWARD_DB_CATALOG_BASELINE)}'::jsonb end;`,
    `    when 'scac-mutation-registry.v15' then '${JSON.stringify(CLAUDE_CONFIG_PRESERVATION_FORWARD_DB_CATALOG_BASELINE)}'::jsonb\n    when '${v16Seal.version}' then '${JSON.stringify(CODEX_COMPACTION_CHECKPOINT_FORWARD_DB_CATALOG_BASELINE)}'::jsonb end;`,
    "Backup guard/status historical catalog case");
  sql = replaceExactlyOnce(sql,
    `    ('scac-mutation-registry.v15','${HISTORICAL_REGISTRY_SEALS.v15.digest}',${HISTORICAL_REGISTRY_SEALS.v15.entryCount},${HISTORICAL_REGISTRY_SEALS.v15.sourceEntryCount})\n`,
    `    ('scac-mutation-registry.v15','${HISTORICAL_REGISTRY_SEALS.v15.digest}',${HISTORICAL_REGISTRY_SEALS.v15.entryCount},${HISTORICAL_REGISTRY_SEALS.v15.sourceEntryCount}),\n    ('${v16Seal.version}','${v16Seal.digest}',${v16Seal.entryCount},${v16Seal.sourceEntryCount})\n`,
    "Backup guard/status historical seal tuple");
  sql = replaceExactlyOnce(sql,
    "    ops.scac_mutation_registry_v15_seal_available()) then",
    "    ops.scac_mutation_registry_v15_seal_available() and\n    ops.scac_mutation_registry_v16_seal_available()) then",
    "Backup guard/status snapshot predecessor seal");
  sql = replaceExactlyOnce(sql,
    `or (r.registry_version='scac-mutation-registry.v17' and r.registry_digest='${v16Seal.digest}')`,
    `or (r.registry_version='scac-mutation-registry.v16' and r.registry_digest='${v16Seal.digest}')\n         or (r.registry_version='scac-mutation-registry.v17' and r.registry_digest='sha256:${v17Digest}')`,
    "Backup guard/status epoch-chain digest cases");
  sql = replaceExactlyOnce(sql,
    `  (registry_version='scac-mutation-registry.v17' and registry_digest='${v16Seal.digest}')`,
    `  (registry_version='scac-mutation-registry.v16' and registry_digest='${v16Seal.digest}') or\n  (registry_version='scac-mutation-registry.v17' and registry_digest='sha256:${v17Digest}')`,
    "Backup guard/status epoch constraint digest cases");
  sql = replaceExactlyOnce(sql,
    `'{registry_digest}',to_jsonb('${v16Seal.digest}'::text)`,
    `'{registry_digest}',to_jsonb('sha256:${v17Digest}'::text)`,
    "Backup guard/status snapshot registry digest");
  sql = replaceExactlyOnce(sql,
    "ops.scac_mutation_registry_v15_seal_available(),ops.scac_mutation_catalog_v17_current()",
    "ops.scac_mutation_registry_v15_seal_available(),ops.scac_mutation_catalog_v16_live_at_seal(),ops.scac_mutation_catalog_v16_current(),ops.scac_mutation_registry_v16_seal_available(),ops.scac_mutation_catalog_v17_current()",
    "Backup guard/status historical function revoke list");
  sql = replaceExactlyOnce(sql,
    "Codex compaction checkpoint successor snapshot: current policy epochs bind mutation registry v17 while historical v2/v3/v4/v5/v6/v7/v8/v9/v10/v11/v12/v13/v14/v15 epochs remain immutable.",
    "Backup guard/status successor snapshot: current policy epochs bind mutation registry v17 while historical v2/v3/v4/v5/v6/v7/v8/v9/v10/v11/v12/v13/v14/v15/v16 epochs remain immutable.",
    "Backup guard/status policy snapshot comment");
  sql = replaceExactlyOnce(sql,
    `(select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v17')<>${v16Seal.entryCount}`,
    `(select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v17')<>${entryCount}`,
    "Backup guard/status v17 entry count guard");
  sql = replaceExactlyOnce(sql,
    `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v15')<>'${HISTORICAL_REGISTRY_SEALS.v15.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v15')<>${HISTORICAL_REGISTRY_SEALS.v15.entryCount} then raise exception 'sealed SCAC mutation registry v15 changed during successor creation'; end if;`,
    `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v16')<>'${v16Seal.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v16')<>${v16Seal.entryCount} then raise exception 'sealed SCAC mutation registry v16 changed during successor creation'; end if;`,
    "Backup guard/status predecessor seal guard");
  sql = replaceExactlyOnce(sql,
    "ops.scac_policy_epoch_snapshot(),ops.scac_policy_epoch_snapshot_v6(),ops.scac_policy_epoch_snapshot_v7(),ops.scac_policy_epoch_snapshot_v8(),ops.scac_policy_epoch_snapshot_v9(),ops.scac_policy_epoch_snapshot_v10(),ops.scac_policy_epoch_snapshot_v11(),ops.scac_policy_epoch_snapshot_v12(),ops.scac_policy_epoch_snapshot_v13(),ops.scac_policy_epoch_snapshot_v14(),ops.scac_policy_epoch_snapshot_v15(),",
    "ops.scac_policy_epoch_snapshot(),ops.scac_policy_epoch_snapshot_v6(),ops.scac_policy_epoch_snapshot_v7(),ops.scac_policy_epoch_snapshot_v8(),ops.scac_policy_epoch_snapshot_v9(),ops.scac_policy_epoch_snapshot_v10(),ops.scac_policy_epoch_snapshot_v11(),ops.scac_policy_epoch_snapshot_v12(),ops.scac_policy_epoch_snapshot_v13(),ops.scac_policy_epoch_snapshot_v14(),ops.scac_policy_epoch_snapshot_v15(),ops.scac_policy_epoch_snapshot_v16(),",
    "Backup guard/status historical policy snapshot revoke list");

  const seedStartMarker = "with seed as (select value as contract from jsonb_array_elements(";
  const seedEndMarker = "::jsonb))\ninsert into ops.scac_mutation_registry_entry";
  const seedStart = sql.indexOf(seedStartMarker);
  const secondSeedStart = sql.indexOf(seedStartMarker, seedStart + seedStartMarker.length);
  const seedEnd = sql.indexOf(seedEndMarker, seedStart + seedStartMarker.length);
  const secondSeedEnd = sql.indexOf(seedEndMarker, seedEnd + seedEndMarker.length);
  if (seedStart < 0 || secondSeedStart >= 0 || seedEnd < 0 || secondSeedEnd >= 0)
    throw new Error("sealed SCAC v16 migration has no exact source-seed boundary");
  const seed = JSON.stringify(rows.map(row => ({ ...row, entry_digest: `sha256:${sha256(row)}` })));
  sql = `${sql.slice(0, seedStart + seedStartMarker.length)}${sqlLiteral(seed)}${sql.slice(seedEnd)}`;

  const preflightCurrent = renderV17Current(BACKUP_GUARD_STATUS_PRE_V17_DB_CATALOG_BASELINE);
  const preflightBegin = preflightCurrent.indexOf("begin\n");
  const preflightEnd = preflightCurrent.lastIndexOf("end $fn$;");
  if (preflightBegin < 0 || preflightEnd <= preflightBegin)
    throw new Error("generated v17 catalog predicate has no exact preflight body boundary");
  let preflightBody = preflightCurrent.slice(preflightBegin + "begin\n".length, preflightEnd);
  preflightBody = preflightBody.replaceAll(
    "then return false; end if;",
    "then raise exception 'Backup guard/status pre-v17 catalog receipt drifted'; end if;");
  preflightBody = replaceExactlyOnce(preflightBody,
    `return observed_count=${BACKUP_GUARD_STATUS_PRE_V17_DB_CATALOG_BASELINE.role_authority.count} and observed_digest='${BACKUP_GUARD_STATUS_PRE_V17_DB_CATALOG_BASELINE.role_authority.digest}';`,
    `if observed_count<>${BACKUP_GUARD_STATUS_PRE_V17_DB_CATALOG_BASELINE.role_authority.count} or observed_digest<>'${BACKUP_GUARD_STATUS_PRE_V17_DB_CATALOG_BASELINE.role_authority.digest}' then raise exception 'Backup guard/status pre-v17 role-authority receipt drifted'; end if;`,
    "Backup guard/status pre-v17 role receipt");
  const predecessorHash = sha256(v16Migration);
  const predecessorPreflight =
`-- Exact disposable-Postgres post-0489 receipt. Refuse before creating any v17 function.
do $backup_guard_status_preflight$
declare observed_count integer; observed_digest text; grant_snapshot jsonb;
begin
  if (select count(*) from public.schema_migrations where filename='0490_codex_compaction_checkpoint_registry_activation.sql')<>1
     or not exists(select 1 from public.schema_migrations where filename='0490_codex_compaction_checkpoint_registry_activation.sql'
       and sha256='${predecessorHash}') then
    raise exception 'Backup guard/status pre-v17 migration ledger receipt drifted';
  end if;
  grant_snapshot:=ops.scac_runtime_dml_grant_snapshot();
  if (grant_snapshot->>'entry_count')::integer<>${BACKUP_GUARD_STATUS_PRE_V17_DB_CATALOG_BASELINE.runtime_dml_grants.count}
     or grant_snapshot->>'grant_digest'<>'${BACKUP_GUARD_STATUS_PRE_V17_DB_CATALOG_BASELINE.runtime_dml_grants.digest}' then
    raise exception 'Backup guard/status pre-v17 runtime grant receipt drifted';
  end if;
${preflightBody}end $backup_guard_status_preflight$;

`;
  return `${predecessorPreflight}${sql}`.replace(/\n+$/, "\n");
}


// ── WR-000068: sourced shape forward correction ─────────────────────────────
//
// The domain half of migration 0492. Everything below is emitted verbatim into
// the generated file so the accepted migration path stays byte-reproducible
// from this generator; the four rebased consumers are lifted from their exact
// Production-applied witness migrations (sha-pinned above) and only the named
// guard predicates are rewritten through replaceExactlyOnce, which refuses a
// witness that no longer carries the predicate it is asked to rebase.

function sourcedShapeWitness(path, witnesses = {}) {
  // 0470 is itself a generated frontier artifact, so a frontier render passes
  // its rendered text here instead of depending on the committed file.
  const source = witnesses[path] ?? readFileSync(resolve(REPO_ROOT, path), "utf8");
  const observed = sha256(source);
  if (observed !== SOURCED_SHAPE_FORWARD_CORRECTION_WITNESS_SHA256[path])
    throw new Error(`sourced shape forward-correction witness changed: ${path}: ${observed}`);
  return source;
}

function sliceFunctionDefinition(source, startMarker, label) {
  const start = source.indexOf(startMarker);
  if (start < 0 || source.indexOf(startMarker, start + startMarker.length) >= 0)
    throw new Error(`${label}: witness function definition is absent or ambiguous`);
  const terminator = "\n$$;\n";
  const end = source.indexOf(terminator, start);
  if (end < 0) throw new Error(`${label}: witness function definition has no terminator`);
  return source.slice(start, end + terminator.length);
}

const SOURCED_SHAPE_RECEIPT_LOOKUP_0306 =
`     and exists (select 1 from ops.sourced_work_request_shape_disposition_receipt r
                  where r.work_request_id=w.id and r.result_version=w.version
                    and (w.shape_disposition,w.shape_fixed_surface_ref,w.shape_rationale,w.shape_decided_by_actor_id,w.shape_decided_at)
                        is not distinct from (r.disposition,r.fixed_surface_ref,r.rationale,r.decided_by_actor_id,r.decided_at))`;
const SOURCED_SHAPE_RECEIPT_LOOKUP_0470 =
`      and exists (select 1 from ops.sourced_work_request_shape_disposition_receipt r where r.work_request_id=w.id and r.result_version=w.version
                    and (w.shape_disposition,w.shape_fixed_surface_ref,w.shape_rationale,w.shape_decided_by_actor_id,w.shape_decided_at)
                        is not distinct from (r.disposition,r.fixed_surface_ref,r.rationale,r.decided_by_actor_id,r.decided_at))`;
const SOURCED_SHAPE_BINDING_GUARD_0333 =
`              and sb.disposition is not distinct from w.shape_disposition
              and sb.fixed_surface_ref is not distinct from w.shape_fixed_surface_ref)) then`;
const SOURCED_SHAPE_BINDING_GUARD_REBASED =
`              and sb.disposition is not distinct from w.shape_disposition
              and sb.fixed_surface_ref is not distinct from w.shape_fixed_surface_ref
              and exists (select 1 from ops.sourced_work_request_shape_disposition_lineage(w.id) e
                           where (e.disposition,e.fixed_surface_ref,e.rationale,e.decided_by_actor_id,e.decided_at)
                             is not distinct from (sb.disposition,sb.fixed_surface_ref,sb.rationale,sb.decided_by_actor_id,sb.decided_at)))) then`;

const SOURCED_SHAPE_FORWARD_CORRECTION_SURFACE_SQL = String.raw`-- WR-000068: append-only forward correction for a sourced shape disposition.
--
-- A sourced triaged Work Request may carry an immutable not_required receipt
-- that later proves incompatible with its intrinsic-heavy classification
-- (WR-000063). The original receipt is preserved byte-for-byte; exactly one
-- linked, monotonic not_required -> required correction is admitted before any
-- Work Shape revision or ready-plan transition. The correction table's unique
-- work_request_id is the durable one-time control. Every consumer of the five
-- shape columns is rebased below on the effective receipt-backed lineage;
-- unsourced Work Requests keep their direct update, Shape, and read behavior.

create table ops.sourced_work_request_shape_disposition_correction_receipt (
  id uuid primary key default gen_random_uuid(),
  work_request_id uuid not null unique references ops.work_request(id),
  idempotency_key uuid not null unique,
  original_receipt_id uuid not null unique references ops.sourced_work_request_shape_disposition_receipt(id),
  base_version integer not null check (base_version > 0),
  result_version integer not null check (result_version = base_version + 1),
  disposition text not null check (disposition = 'required'),
  fixed_surface_ref text check (fixed_surface_ref is null),
  rationale text not null check (btrim(rationale) <> ''),
  decided_by_actor_id uuid not null references public.actor(id),
  decided_at timestamptz not null default now()
);

comment on table ops.sourced_work_request_shape_disposition_correction_receipt is
  'Private append-only one-time forward correction linked to an immutable sourced not_required receipt. '
  'Unique work_request_id is the durable one-time control across SCAC registry identities.';

create trigger sourced_work_request_shape_disposition_correction_immutable
before update or delete on ops.sourced_work_request_shape_disposition_correction_receipt
for each row execute function ops.sourced_work_shape_receipts_are_immutable();

-- Structural lineage: the original receipt and, when present, its one linked
-- correction. Zero rows when the request holds no receipt; an exception when
-- the receipts disagree with each other, because an ambiguous lineage must
-- never resolve to either side. It does not compare against the Work Request
-- row, so ready-state consumers can read the lineage that a binding froze.
create or replace function ops.sourced_work_request_shape_disposition_lineage(p_work_request_id uuid)
returns table (
  work_request_id uuid, original_receipt_id uuid, correction_receipt_id uuid,
  receipt_kind text, disposition text, fixed_surface_ref text, rationale text,
  decided_by_actor_id uuid, decided_at timestamptz, base_version integer, result_version integer
)
language plpgsql stable security definer
set search_path = pg_catalog, ops
as $$
declare
  original ops.sourced_work_request_shape_disposition_receipt%rowtype;
  correction ops.sourced_work_request_shape_disposition_correction_receipt%rowtype;
begin
  if p_work_request_id is null then return; end if;
  select r.* into original from ops.sourced_work_request_shape_disposition_receipt r
   where r.work_request_id = p_work_request_id;
  if not found then return; end if;
  select c.* into correction from ops.sourced_work_request_shape_disposition_correction_receipt c
   where c.work_request_id = p_work_request_id;
  if not found then
    if exists (select 1 from ops.sourced_work_request_shape_disposition_correction_receipt c
                where c.original_receipt_id = original.id) then
      raise exception 'sourced shape disposition lineage is ambiguous';
    end if;
    return query select original.work_request_id, original.id, null::uuid, 'original'::text,
      original.disposition, original.fixed_surface_ref, original.rationale,
      original.decided_by_actor_id, original.decided_at, original.base_version, original.result_version;
    return;
  end if;
  if correction.original_receipt_id is distinct from original.id
     or correction.base_version is distinct from original.result_version
     or correction.result_version is distinct from original.result_version + 1
     or original.disposition is distinct from 'not_required'
     or correction.disposition is distinct from 'required'
     or correction.fixed_surface_ref is not null then
    raise exception 'sourced shape disposition lineage is ambiguous';
  end if;
  return query select original.work_request_id, original.id, correction.id, 'correction'::text,
    correction.disposition, correction.fixed_surface_ref, correction.rationale,
    correction.decided_by_actor_id, correction.decided_at, correction.base_version, correction.result_version;
end;
$$;

-- Effective backed disposition for one exact Work Request row image. Callers
-- pass the row they are judging: NEW inside a BEFORE UPDATE trigger, or the
-- row they just locked. One row is returned only when the effective receipt
-- (the correction when it exists, else the original) is exactly current for
-- that image: same version and the same five shape fields. Stale, mismatched,
-- unsourced, or unbacked images return no row, so every consumer that wraps
-- this in exists() fails closed.
create or replace function ops.effective_sourced_work_request_shape_disposition(p_work_request ops.work_request)
returns table (
  work_request_id uuid, original_receipt_id uuid, correction_receipt_id uuid,
  receipt_kind text, disposition text, fixed_surface_ref text, rationale text,
  decided_by_actor_id uuid, decided_at timestamptz, base_version integer, result_version integer
)
language plpgsql stable security definer
set search_path = pg_catalog, ops
as $$
declare
  e record;
begin
  if p_work_request.id is null or p_work_request.capture_idempotency_key is null then return; end if;
  select l.* into e from ops.sourced_work_request_shape_disposition_lineage(p_work_request.id) l;
  if not found then return; end if;
  if e.result_version is distinct from p_work_request.version
     or (p_work_request.shape_disposition, p_work_request.shape_fixed_surface_ref, p_work_request.shape_rationale,
         p_work_request.shape_decided_by_actor_id, p_work_request.shape_decided_at)
        is distinct from (e.disposition, e.fixed_surface_ref, e.rationale, e.decided_by_actor_id, e.decided_at) then
    return;
  end if;
  return query select e.work_request_id, e.original_receipt_id, e.correction_receipt_id, e.receipt_kind,
    e.disposition, e.fixed_surface_ref, e.rationale, e.decided_by_actor_id, e.decided_at,
    e.base_version, e.result_version;
end;
$$;

-- The sole sourced-receipt seam keeps its exact seven-argument signature,
-- return shape, and carr_writer-only grant. The initial disposition path is
-- 0306's, plus one refusal: an intrinsically heavy request may not record
-- not_required, because heavy work needs a Work Shape. The new path admits one
-- not_required -> required correction with no classifier refusal, which is
-- what lets an already-mistaken heavy receipt be repaired.
create or replace function ops.set_sourced_work_request_shape_disposition(
  p_work_request text,
  p_base_version integer,
  p_disposition text,
  p_fixed_surface_ref text,
  p_rationale text,
  p_decided_by_actor_id uuid,
  p_idempotency_key uuid
)
returns table (
  work_request_id uuid,
  ref text,
  state text,
  version integer,
  shape_disposition text,
  shape_fixed_surface_ref text,
  shape_rationale text,
  shape_decided_by_actor_id uuid,
  shape_decided_at timestamptz,
  replayed boolean
)
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  w ops.work_request%rowtype;
  original ops.sourced_work_request_shape_disposition_receipt%rowtype;
  correction ops.sourced_work_request_shape_disposition_correction_receipt%rowtype;
  actor public.actor%rowtype;
  classification jsonb;
  normalized_fixed_surface text := nullif(btrim(coalesce(p_fixed_surface_ref,'')), '');
  normalized_rationale text := nullif(btrim(coalesce(p_rationale,'')), '');
begin
  if coalesce(btrim(p_work_request),'') !~ '^WR-[0-9]{1,12}$'
     or p_base_version is null or p_base_version < 1
     or p_disposition not in ('required','not_required')
     or normalized_rationale is null
     or p_decided_by_actor_id is null
     or p_idempotency_key is null
     or (p_disposition = 'required' and normalized_fixed_surface is not null)
     or (p_disposition = 'not_required' and normalized_fixed_surface is null) then
    raise exception 'sourced shape disposition requires exact Work Request/base version, closed disposition, exact fixed surface rule, rationale, active actor, and UUID idempotency key';
  end if;

  select a.* into actor from public.actor a
   where a.id = p_decided_by_actor_id and a.active
   for share;
  if not found then
    raise exception 'sourced shape disposition actor is not active';
  end if;

  -- One advisory lock on the caller key precedes BOTH receipt lookups, so two
  -- first calls with the same key serialize before either can observe an
  -- empty table, and a key can never be reused across the two tables.
  perform pg_advisory_xact_lock(hashtextextended('program6-sourced-shape-disposition:' || p_idempotency_key, 0));
  select r.* into original
    from ops.sourced_work_request_shape_disposition_receipt r
   where r.idempotency_key = p_idempotency_key
   for share;
  if found then
    select x.* into w from ops.work_request x where x.id = original.work_request_id for share;
    if not found
       or w.ref is distinct from p_work_request
       or original.base_version is distinct from p_base_version
       or original.disposition is distinct from p_disposition
       or original.fixed_surface_ref is distinct from normalized_fixed_surface
       or original.rationale is distinct from normalized_rationale
       or original.decided_by_actor_id is distinct from p_decided_by_actor_id
       or w.state is distinct from 'triaged'
       or w.version is distinct from original.result_version
       or (w.shape_disposition,w.shape_fixed_surface_ref,w.shape_rationale,w.shape_decided_by_actor_id,w.shape_decided_at)
          is distinct from
          (original.disposition,original.fixed_surface_ref,original.rationale,original.decided_by_actor_id,original.decided_at) then
      raise exception 'idempotency key already names a different sourced shape disposition';
    end if;
    return query select w.id,w.ref,w.state,w.version,w.shape_disposition,w.shape_fixed_surface_ref,
      w.shape_rationale,w.shape_decided_by_actor_id,w.shape_decided_at,true;
    return;
  end if;
  select c.* into correction
    from ops.sourced_work_request_shape_disposition_correction_receipt c
   where c.idempotency_key = p_idempotency_key
   for share;
  if found then
    select x.* into w from ops.work_request x where x.id = correction.work_request_id for share;
    if not found
       or w.ref is distinct from p_work_request
       or correction.base_version is distinct from p_base_version
       or p_disposition is distinct from 'required'
       or normalized_fixed_surface is not null
       or correction.rationale is distinct from normalized_rationale
       or correction.decided_by_actor_id is distinct from p_decided_by_actor_id
       or w.state is distinct from 'triaged'
       or w.version is distinct from correction.result_version
       or (w.shape_disposition,w.shape_fixed_surface_ref,w.shape_rationale,w.shape_decided_by_actor_id,w.shape_decided_at)
          is distinct from
          (correction.disposition,correction.fixed_surface_ref,correction.rationale,correction.decided_by_actor_id,correction.decided_at) then
      raise exception 'idempotency key already names a different sourced shape correction';
    end if;
    return query select w.id,w.ref,w.state,w.version,w.shape_disposition,w.shape_fixed_surface_ref,
      w.shape_rationale,w.shape_decided_by_actor_id,w.shape_decided_at,true;
    return;
  end if;

  select x.* into w from ops.work_request x
   where x.ref = p_work_request
   for update;
  if not found
     or w.capture_idempotency_key is null
     or w.organization_tenant_id is distinct from 'carr-internal'
     or w.state is distinct from 'triaged'
     or w.version is distinct from p_base_version
     or w.program_key is not null or w.program_ordinal is not null then
    raise exception 'exact current triaged sourced Work Request required';
  end if;

  select r.* into original
    from ops.sourced_work_request_shape_disposition_receipt r
   where r.work_request_id = w.id
   for share;
  if found then
    -- Forward correction: exactly one monotonic not_required -> required on
    -- the exact current receipt-backed row, before any Shape revision, with
    -- no prior correction. No classifier call: an intrinsically heavy request
    -- is precisely the one that must be able to move to required.
    if p_disposition is distinct from 'required'
       or normalized_fixed_surface is not null
       or original.disposition is distinct from 'not_required'
       or original.result_version is distinct from w.version
       or (w.shape_disposition,w.shape_fixed_surface_ref,w.shape_rationale,w.shape_decided_by_actor_id,w.shape_decided_at)
          is distinct from
          (original.disposition,original.fixed_surface_ref,original.rationale,original.decided_by_actor_id,original.decided_at)
       or exists (select 1 from ops.work_shape_revision sr where sr.work_request_id = w.id)
       or exists (select 1 from ops.sourced_work_request_shape_disposition_correction_receipt c
                   where c.work_request_id = w.id or c.original_receipt_id = original.id) then
      raise exception 'only an exact current sourced not_required receipt may receive one required correction before Shape';
    end if;
    insert into ops.sourced_work_request_shape_disposition_correction_receipt
      (work_request_id,idempotency_key,original_receipt_id,base_version,result_version,disposition,fixed_surface_ref,rationale,decided_by_actor_id)
    values
      (w.id,p_idempotency_key,original.id,w.version,w.version + 1,'required',null,normalized_rationale,p_decided_by_actor_id)
    returning * into correction;
    update ops.work_request x
       set shape_disposition = correction.disposition,
           shape_fixed_surface_ref = correction.fixed_surface_ref,
           shape_rationale = correction.rationale,
           shape_decided_by_actor_id = correction.decided_by_actor_id,
           shape_decided_at = correction.decided_at,
           version = correction.result_version,
           updated_at = now()
     where x.id = w.id;
  else
    if (w.shape_disposition,w.shape_fixed_surface_ref,w.shape_rationale,w.shape_decided_by_actor_id,w.shape_decided_at)
         is distinct from (null::text,null::text,null::text,null::uuid,null::timestamptz)
       or exists (select 1 from ops.work_shape_revision sr where sr.work_request_id = w.id) then
      raise exception 'only the exact current unshaped triaged sourced Work Request may record a shape disposition';
    end if;
    if p_disposition = 'not_required' then
      classification := ops.heavy_build_classification(w.id, '', '[]'::jsonb, '{}'::jsonb);
      if classification is null or classification->>'tier' is distinct from 'standard' then
        raise exception 'an intrinsically heavy sourced Work Request requires a Work Shape; not_required is refused';
      end if;
    end if;
    insert into ops.sourced_work_request_shape_disposition_receipt
      (work_request_id,idempotency_key,base_version,result_version,disposition,fixed_surface_ref,rationale,decided_by_actor_id)
    values
      (w.id,p_idempotency_key,p_base_version,w.version + 1,p_disposition,normalized_fixed_surface,normalized_rationale,p_decided_by_actor_id)
    returning * into original;
    update ops.work_request x
       set shape_disposition = original.disposition,
           shape_fixed_surface_ref = original.fixed_surface_ref,
           shape_rationale = original.rationale,
           shape_decided_by_actor_id = original.decided_by_actor_id,
           shape_decided_at = original.decided_at,
           version = original.result_version,
           updated_at = now()
     where x.id = w.id;
  end if;
  select x.* into w from ops.work_request x where x.id = w.id;
  return query select w.id,w.ref,w.state,w.version,w.shape_disposition,w.shape_fixed_surface_ref,
    w.shape_rationale,w.shape_decided_by_actor_id,w.shape_decided_at,false;
end;
$$;

`;

const SOURCED_SHAPE_FORWARD_CORRECTION_TAIL_SQL = String.raw`-- Once a sourced Work Request holds a disposition receipt, a Work Shape
-- revision must follow the EFFECTIVE receipt-backed required disposition at
-- the exact current version: a Shape can neither precede the correction of a
-- mistaken not_required receipt nor attach to a stale version. A sourced
-- request that holds no receipt yet has no lineage to bind; it stays under the
-- 0132 column gates as before, and the public write-work-shape verb already
-- refuses it through the lineage projection. Unsourced requests are untouched.
create or replace function ops.sourced_work_shape_revision_requires_effective_required()
returns trigger language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  w ops.work_request%rowtype;
begin
  select x.* into w from ops.work_request x where x.id = new.work_request_id;
  if not found or w.capture_idempotency_key is null
     or not exists (select 1 from ops.sourced_work_request_shape_disposition_receipt r
                     where r.work_request_id = w.id) then
    return new;
  end if;
  if new.work_request_version is distinct from w.version
     or not exists (select 1 from ops.effective_sourced_work_request_shape_disposition(w) e
                     where e.disposition = 'required') then
    raise exception 'a sourced Work Shape revision requires the exact current receipt-backed required disposition';
  end if;
  return new;
end;
$$;

create trigger sourced_work_shape_revision_requires_effective_required
before insert on ops.work_shape_revision
for each row execute function ops.sourced_work_shape_revision_requires_effective_required();

-- The only runtime-readable lineage surface: original and correction
-- summaries plus the effective marker for a sourced Work Request, null for an
-- unsourced one. Idempotency keys stay private; the receipt tables stay denied.
create or replace function ops.read_sourced_work_request_shape_disposition_lineage(p_work_request_id uuid)
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops
as $$
declare
  w ops.work_request%rowtype;
  original ops.sourced_work_request_shape_disposition_receipt%rowtype;
  correction ops.sourced_work_request_shape_disposition_correction_receipt%rowtype;
  e record;
  backed boolean;
begin
  select x.* into w from ops.work_request x where x.id = p_work_request_id;
  if not found or w.capture_idempotency_key is null then return null; end if;
  select r.* into original from ops.sourced_work_request_shape_disposition_receipt r where r.work_request_id = w.id;
  if not found then
    return jsonb_build_object('status','none','effective',null,'original',null,'correction',null,
      'backs_current_version',false);
  end if;
  select c.* into correction from ops.sourced_work_request_shape_disposition_correction_receipt c where c.work_request_id = w.id;
  select l.* into e from ops.sourced_work_request_shape_disposition_lineage(w.id) l;
  backed := exists (select 1 from ops.effective_sourced_work_request_shape_disposition(w));
  return jsonb_build_object(
    'status', case when e.receipt_kind = 'correction' then 'corrected' else 'original' end,
    'effective', jsonb_build_object('receipt_kind', e.receipt_kind,
      'receipt_id', coalesce(e.correction_receipt_id, e.original_receipt_id),
      'disposition', e.disposition, 'fixed_surface_ref', e.fixed_surface_ref, 'rationale', e.rationale,
      'decided_by_actor_id', e.decided_by_actor_id, 'decided_at', e.decided_at,
      'base_version', e.base_version, 'result_version', e.result_version),
    'original', jsonb_build_object('receipt_id', original.id, 'disposition', original.disposition,
      'fixed_surface_ref', original.fixed_surface_ref, 'rationale', original.rationale,
      'decided_by_actor_id', original.decided_by_actor_id, 'decided_at', original.decided_at,
      'base_version', original.base_version, 'result_version', original.result_version),
    'correction', case when correction.id is null then null::jsonb else jsonb_build_object(
      'receipt_id', correction.id, 'original_receipt_id', correction.original_receipt_id,
      'disposition', correction.disposition, 'fixed_surface_ref', correction.fixed_surface_ref,
      'rationale', correction.rationale, 'decided_by_actor_id', correction.decided_by_actor_id,
      'decided_at', correction.decided_at, 'base_version', correction.base_version,
      'result_version', correction.result_version) end,
    'backs_current_version', backed);
end;
$$;

revoke all on table ops.sourced_work_request_shape_disposition_correction_receipt
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.sourced_work_request_shape_disposition_lineage(uuid),
  ops.effective_sourced_work_request_shape_disposition(ops.work_request),
  ops.sourced_work_shape_revision_requires_effective_required(),
  ops.read_sourced_work_request_shape_disposition_lineage(uuid)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.read_sourced_work_request_shape_disposition_lineage(uuid) to carr_reader,carr_writer;
revoke all on function ops.set_sourced_work_request_shape_disposition(text,integer,text,text,text,uuid,uuid)
  from public,carr_reader,carr_jobs,carr_authority;
grant execute on function ops.set_sourced_work_request_shape_disposition(text,integer,text,text,text,uuid,uuid)
  to carr_writer;

do $wr68_privilege_boundary$
begin
  if (select count(*) from pg_proc p join pg_namespace n on n.oid=p.pronamespace
       where n.nspname='ops' and p.proname='set_sourced_work_request_shape_disposition') <> 1
     or pg_get_function_identity_arguments('ops.set_sourced_work_request_shape_disposition(text,integer,text,text,text,uuid,uuid)'::regprocedure)
        <> 'p_work_request text, p_base_version integer, p_disposition text, p_fixed_surface_ref text, p_rationale text, p_decided_by_actor_id uuid, p_idempotency_key uuid'
     or not has_function_privilege('carr_writer','ops.set_sourced_work_request_shape_disposition(text,integer,text,text,text,uuid,uuid)','execute')
     or has_function_privilege('carr_reader','ops.set_sourced_work_request_shape_disposition(text,integer,text,text,text,uuid,uuid)','execute')
     or has_function_privilege('carr_jobs','ops.set_sourced_work_request_shape_disposition(text,integer,text,text,text,uuid,uuid)','execute')
     or has_function_privilege('carr_authority','ops.set_sourced_work_request_shape_disposition(text,integer,text,text,text,uuid,uuid)','execute')
     or has_table_privilege('carr_reader','ops.sourced_work_request_shape_disposition_correction_receipt','select')
     or has_table_privilege('carr_writer','ops.sourced_work_request_shape_disposition_correction_receipt','select')
     or has_table_privilege('carr_writer','ops.sourced_work_request_shape_disposition_receipt','select')
     or has_function_privilege('carr_writer','ops.effective_sourced_work_request_shape_disposition(ops.work_request)','execute')
     or has_function_privilege('carr_writer','ops.sourced_work_request_shape_disposition_lineage(uuid)','execute')
     or not has_function_privilege('carr_reader','ops.read_sourced_work_request_shape_disposition_lineage(uuid)','execute')
     or not has_function_privilege('carr_writer','ops.read_sourced_work_request_shape_disposition_lineage(uuid)','execute')
     or has_function_privilege('carr_jobs','ops.read_sourced_work_request_shape_disposition_lineage(uuid)','execute') then
    raise exception '0492 FAILED: sourced shape forward-correction privilege boundary is not narrow';
  end if;
end $wr68_privilege_boundary$;

`;

export function renderSourcedShapeForwardCorrectionDomainSql(witnesses = {}) {
  const shape0306 = sourcedShapeWitness("migrations/0306_sourced_work_shape_disposition.sql", witnesses);
  const guards0333 = sourcedShapeWitness("migrations/0333_shape_preserving_outcome_guards.sql", witnesses);
  const withdrawal0426 = sourcedShapeWitness("migrations/0426_withdraw_a_work_request_captured_in_error.sql", witnesses);
  const proposal0470 = sourcedShapeWitness("migrations/0470_source_merge_authority_projection.sql", witnesses);

  // 0426 immutability trigger: the triaged shape-disposition arm accepts the
  // receipt that backs NEW, whether that is the original or the correction.
  let immutable = sliceFunctionDefinition(withdrawal0426,
    "CREATE OR REPLACE FUNCTION ops.sourced_work_request_is_immutable() RETURNS trigger",
    "0426 sourced_work_request_is_immutable");
  immutable = replaceExactlyOnce(immutable,
`     and exists (
       select 1 from ops.sourced_work_request_shape_disposition_receipt r
        where r.work_request_id = old.id and r.base_version = old.version and r.result_version = new.version
          and (new.shape_disposition,new.shape_fixed_surface_ref,new.shape_rationale,new.shape_decided_by_actor_id,new.shape_decided_at)
             is not distinct from
             (r.disposition,r.fixed_surface_ref,r.rationale,r.decided_by_actor_id,r.decided_at)
     ) then`,
`     and exists (
       select 1 from ops.effective_sourced_work_request_shape_disposition(new) e
        where e.base_version = old.version and e.result_version = new.version
     ) then`,
    "0426 triaged shape-disposition arm");

  // 0470 proposal: both receipt-backed shape arms read the effective lineage.
  let propose = sliceFunctionDefinition(proposal0470,
    "create or replace function ops.propose_sourced_work_request_plan(",
    "0470 propose_sourced_work_request_plan");
  propose = replaceExactlyOnce(propose,
`    (w.shape_disposition='required' and w.shape_fixed_surface_ref is null and w.shape_rationale is not null and btrim(w.shape_rationale) <> ''
${SOURCED_SHAPE_RECEIPT_LOOKUP_0470}`,
`    (w.shape_disposition='required' and w.shape_fixed_surface_ref is null and w.shape_rationale is not null and btrim(w.shape_rationale) <> ''
      and exists (select 1 from ops.effective_sourced_work_request_shape_disposition(w) e where e.disposition='required')`,
    "0470 required shape arm");
  propose = replaceExactlyOnce(propose, SOURCED_SHAPE_RECEIPT_LOOKUP_0470,
`      and exists (select 1 from ops.effective_sourced_work_request_shape_disposition(w) e where e.disposition='not_required')`,
    "0470 not_required shape arm");

  // 0306 acceptance: the preserve-shape decision reads the effective lineage;
  // the human authority check and every other line stay exact.
  let accept = sliceFunctionDefinition(shape0306,
    "create or replace function ops.accept_sourced_work_request_plan(",
    "0306 accept_sourced_work_request_plan");
  accept = replaceExactlyOnce(accept,
`     and w.shape_decided_by_actor_id is not null and w.shape_decided_at is not null
${SOURCED_SHAPE_RECEIPT_LOOKUP_0306}`,
`     and w.shape_decided_by_actor_id is not null and w.shape_decided_at is not null
     and exists (select 1 from ops.effective_sourced_work_request_shape_disposition(w) e where e.disposition='required')`,
    "0306 required preserve-shape arm");
  accept = replaceExactlyOnce(accept, SOURCED_SHAPE_RECEIPT_LOOKUP_0306,
`     and exists (select 1 from ops.effective_sourced_work_request_shape_disposition(w) e where e.disposition='not_required')`,
    "0306 not_required preserve-shape arm");

  // 0333 outcome guards: a shape binding must also equal the effective lineage.
  let proposeOutcome = sliceFunctionDefinition(guards0333,
    "create or replace function ops.propose_sourced_work_request_outcome_feedback(",
    "0333 propose_sourced_work_request_outcome_feedback");
  proposeOutcome = replaceExactlyOnce(proposeOutcome, SOURCED_SHAPE_BINDING_GUARD_0333,
    SOURCED_SHAPE_BINDING_GUARD_REBASED, "0333 outcome proposal shape binding guard");
  let acceptOutcome = sliceFunctionDefinition(guards0333,
    "create or replace function ops.accept_sourced_work_request_outcome_feedback(",
    "0333 accept_sourced_work_request_outcome_feedback");
  acceptOutcome = replaceExactlyOnce(acceptOutcome, SOURCED_SHAPE_BINDING_GUARD_0333,
    SOURCED_SHAPE_BINDING_GUARD_REBASED, "0333 outcome acceptance shape binding guard");

  return `${SOURCED_SHAPE_FORWARD_CORRECTION_SURFACE_SQL}` +
    "-- Consumers rebased on the effective lineage. Each definition is the exact\n" +
    "-- Production-applied witness with only the named guard predicate rewritten.\n\n" +
    `${immutable}\n${propose}\n${accept}\n${proposeOutcome}\n${acceptOutcome}\n` +
    `${SOURCED_SHAPE_FORWARD_CORRECTION_TAIL_SQL}`;
}

export function renderSourcedShapeForwardCorrectionRegistrySql(rows = fullInventory(),
  dbCatalogBaseline = SOURCED_SHAPE_FORWARD_CORRECTION_FORWARD_DB_CATALOG_BASELINE,
  predecessorArtifacts = undefined) {
  const { v17: v17Seal } = HISTORICAL_REGISTRY_SEALS;
  const v18Digest = registryDigestFor(REGISTRY_V18_VERSION, rows, dbCatalogBaseline);
  const catalogCount = dbCatalogBaseline.secdef_execute.count +
    dbCatalogBaseline.relation_dml.count + dbCatalogBaseline.column_dml.count;
  const entryCount = rows.length + catalogCount;
  const v17MigrationPath = "migrations/0491_backup_guard_status_registry_activation.sql";
  const v17RuntimePath = "mcp-server/src/scac-mutation-registry.v17.generated.js";
  const v17Rows = frozenInventory(REGISTRY_V17_VERSION);
  const v17Migration = predecessorArtifacts?.migration ??
    renderBackupGuardStatusForwardRegistrySql(
      v17Rows, BACKUP_GUARD_STATUS_FORWARD_DB_CATALOG_BASELINE);
  const v17Runtime = predecessorArtifacts?.runtime ?? renderRuntimeProjection(v17Rows, {
    version: REGISTRY_V17_VERSION,
    dbCatalogBaseline: BACKUP_GUARD_STATUS_FORWARD_DB_CATALOG_BASELINE,
  });
  for (const [path, source] of [
    [v17MigrationPath, v17Migration], [v17RuntimePath, v17Runtime],
  ]) {
    const observed = sha256(source);
    if (observed !== HISTORICAL_REGISTRY_ARTIFACT_SHA256[path])
      throw new Error(`sealed historical SCAC v17 artifact changed: ${path}: ${observed}`);
  }

  const headerMarker =
    "-- SCAC-12: forward-only mutation registry v17 after backup guard/status repair.";
  const coreStart = v17Migration.indexOf(headerMarker);
  if (coreStart < 0 || v17Migration.indexOf(headerMarker, coreStart + headerMarker.length) >= 0)
    throw new Error("sealed SCAC v17 migration has no exact successor core boundary");
  const v17Core = v17Migration.slice(coreStart);
  const currentV17Marker = "create or replace function ops.scac_mutation_catalog_v17_current()";
  const policyMarker =
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v16;";
  const currentV17Start = v17Core.indexOf(currentV17Marker);
  const secondCurrentV17 = v17Core.indexOf(
    currentV17Marker, currentV17Start + currentV17Marker.length);
  const v16HistoryMarker =
    "alter function ops.scac_mutation_catalog_v16_current() rename to scac_mutation_catalog_v16_live_at_seal;";
  const v16HistoryStart = v17Core.indexOf(v16HistoryMarker);
  const secondV16History = v17Core.indexOf(
    v16HistoryMarker, v16HistoryStart + v16HistoryMarker.length);
  const policyStart = v17Core.indexOf(policyMarker);
  const secondPolicy = v17Core.indexOf(policyMarker, policyStart + policyMarker.length);
  if (v16HistoryStart < 0 || secondV16History >= 0 || currentV17Start <= v16HistoryStart ||
      secondCurrentV17 >= 0 || policyStart <= currentV17Start || secondPolicy >= 0)
    throw new Error("sealed SCAC v17 migration has no exact catalog successor boundary");
  const installedV16History = v17Core.slice(v16HistoryStart, currentV17Start);
  const v17Current = v17Core.slice(currentV17Start, policyStart);
  const v17History =
`alter function ops.scac_mutation_catalog_v17_current() rename to scac_mutation_catalog_v17_live_at_seal;
create or replace function ops.scac_mutation_registry_v17_seal_available()
returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$
  select ops.scac_mutation_registry_seal_valid('scac-mutation-registry.v17')
$fn$;
create or replace function ops.scac_mutation_catalog_v17_current()
returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$
  select ops.scac_mutation_catalog_v17_live_at_seal()
$fn$;
comment on function ops.scac_mutation_registry_v17_seal_available() is 'Exact immutable v17 registry seal; separate from whether the live catalog still equals v17.';
comment on function ops.scac_mutation_catalog_v17_current() is 'Historical v17 live-catalog validator; expected to become false after the v18 authority surface is installed.';

`;
  const renderV18Current = baseline => {
    let current = v17Current
      .replaceAll("scac_mutation_catalog_v17_current", "scac_mutation_catalog_v18_current")
      .replaceAll("scac-mutation-registry.v17", "scac-mutation-registry.v18");
    current = replaceExactlyOnce(current,
      `if observed_count<>${BACKUP_GUARD_STATUS_FORWARD_DB_CATALOG_BASELINE.secdef_execute.count} or observed_digest<>'${BACKUP_GUARD_STATUS_FORWARD_DB_CATALOG_BASELINE.secdef_execute.digest}' then return false; end if;`,
      `if observed_count<>${baseline.secdef_execute.count} or observed_digest<>'${baseline.secdef_execute.digest}' then return false; end if;`,
      "Sourced shape forward-correction v18 security-definer baseline");
    current = replaceExactlyOnce(current,
      `if observed_count<>${BACKUP_GUARD_STATUS_FORWARD_DB_CATALOG_BASELINE.relation_dml.count} or observed_digest<>'${BACKUP_GUARD_STATUS_FORWARD_DB_CATALOG_BASELINE.relation_dml.digest}' then return false; end if;`,
      `if observed_count<>${baseline.relation_dml.count} or observed_digest<>'${baseline.relation_dml.digest}' then return false; end if;`,
      "Sourced shape forward-correction v18 relation baseline");
    current = replaceExactlyOnce(current,
      `if observed_count<>${BACKUP_GUARD_STATUS_FORWARD_DB_CATALOG_BASELINE.column_dml.count} or observed_digest<>'${BACKUP_GUARD_STATUS_FORWARD_DB_CATALOG_BASELINE.column_dml.digest}' then return false; end if;`,
      `if observed_count<>${baseline.column_dml.count} or observed_digest<>'${baseline.column_dml.digest}' then return false; end if;`,
      "Sourced shape forward-correction v18 column baseline");
    return replaceExactlyOnce(current,
      `return observed_count=${BACKUP_GUARD_STATUS_FORWARD_DB_CATALOG_BASELINE.role_authority.count} and observed_digest='${BACKUP_GUARD_STATUS_FORWARD_DB_CATALOG_BASELINE.role_authority.digest}';`,
      `return observed_count=${baseline.role_authority.count} and observed_digest='${baseline.role_authority.digest}';`,
      "Sourced shape forward-correction v18 role-authority baseline");
  };
  const v18Current = renderV18Current(dbCatalogBaseline);

  let sql = replaceExactlyOnce(v17Core, v17Current,
    "__BACKUP_GUARD_STATUS_V17_CATALOG_SUCCESSOR__",
    "Sourced shape forward-correction v17 current catalog block");
  sql = replaceExactlyOnce(sql, installedV16History, "",
    "Sourced shape forward-correction already-installed v16 catalog history");
  sql = replaceExactlyOnce(sql, headerMarker,
    "-- SCAC-12: forward-only mutation registry v18 after sourced shape forward correction.",
    "Sourced shape forward-correction migration header");
  sql = sql
    .replaceAll("scac-mutation-registry.v17", "scac-mutation-registry.v18")
    .replaceAll("_v17", "_v18")
    .replaceAll(" v17", " v18");
  sql = replaceExactlyOnce(sql, JSON.stringify(BACKUP_GUARD_STATUS_FORWARD_DB_CATALOG_BASELINE),
    JSON.stringify(dbCatalogBaseline), "Sourced shape forward-correction v18 catalog projection");
  sql = replaceExactlyOnce(sql,
    `'${v17Seal.digest}',${v17Seal.entryCount},${v17Seal.sourceEntryCount},`,
    `'sha256:${v18Digest}',${entryCount},${rows.length},`,
    "Sourced shape forward-correction v18 registry row");
  sql = replaceExactlyOnce(sql,
    `ops.scac_mutation_registration_v18('${v17Seal.digest}',`,
    `ops.scac_mutation_registration_v18('sha256:${v18Digest}',`,
    "Sourced shape forward-correction v18 snapshot registry lookup");
  sql = replaceExactlyOnce(sql,
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v16;",
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v17;",
    "Sourced shape forward-correction policy snapshot predecessor");
  sql = replaceExactlyOnce(sql, "__BACKUP_GUARD_STATUS_V17_CATALOG_SUCCESSOR__",
    `${v17History}${v18Current}`, "Sourced shape forward-correction v17 catalog history insertion");

  const versionsThrough17 = Array.from({ length: 17 }, (_, index) =>
    `'scac-mutation-registry.v${index + 1}'`).join(",");
  const versionsThrough16 = Array.from({ length: 16 }, (_, index) =>
    `'scac-mutation-registry.v${index + 1}'`).join(",");
  sql = replaceExactlyOnce(sql,
    `check (registry_version in (${versionsThrough16},'scac-mutation-registry.v18'))`,
    `check (registry_version in (${versionsThrough17},'scac-mutation-registry.v18'))`,
    "Sourced shape forward-correction registry-version constraint");
  sql = replaceExactlyOnce(sql,
    `if p_registry_version not in (${versionsThrough16}) then return false; end if;`,
    `if p_registry_version not in (${versionsThrough17}) then return false; end if;`,
    "Sourced shape forward-correction historical seal allowlist");
  sql = replaceExactlyOnce(sql,
    `    when 'scac-mutation-registry.v16' then '${HISTORICAL_REGISTRY_SEALS.v16.digest}' end;`,
    `    when 'scac-mutation-registry.v16' then '${HISTORICAL_REGISTRY_SEALS.v16.digest}'\n    when '${v17Seal.version}' then '${v17Seal.digest}' end;`,
    "Sourced shape forward-correction historical digest case");
  sql = replaceExactlyOnce(sql,
    `    when 'scac-mutation-registry.v16' then '${JSON.stringify(CODEX_COMPACTION_CHECKPOINT_FORWARD_DB_CATALOG_BASELINE)}'::jsonb end;`,
    `    when 'scac-mutation-registry.v16' then '${JSON.stringify(CODEX_COMPACTION_CHECKPOINT_FORWARD_DB_CATALOG_BASELINE)}'::jsonb\n    when '${v17Seal.version}' then '${JSON.stringify(BACKUP_GUARD_STATUS_FORWARD_DB_CATALOG_BASELINE)}'::jsonb end;`,
    "Sourced shape forward-correction historical catalog case");
  sql = replaceExactlyOnce(sql,
    `    ('scac-mutation-registry.v16','${HISTORICAL_REGISTRY_SEALS.v16.digest}',${HISTORICAL_REGISTRY_SEALS.v16.entryCount},${HISTORICAL_REGISTRY_SEALS.v16.sourceEntryCount})\n`,
    `    ('scac-mutation-registry.v16','${HISTORICAL_REGISTRY_SEALS.v16.digest}',${HISTORICAL_REGISTRY_SEALS.v16.entryCount},${HISTORICAL_REGISTRY_SEALS.v16.sourceEntryCount}),\n    ('${v17Seal.version}','${v17Seal.digest}',${v17Seal.entryCount},${v17Seal.sourceEntryCount})\n`,
    "Sourced shape forward-correction historical seal tuple");
  sql = replaceExactlyOnce(sql,
    "    ops.scac_mutation_registry_v16_seal_available()) then",
    "    ops.scac_mutation_registry_v16_seal_available() and\n    ops.scac_mutation_registry_v17_seal_available()) then",
    "Sourced shape forward-correction snapshot predecessor seal");
  sql = replaceExactlyOnce(sql,
    `or (r.registry_version='scac-mutation-registry.v18' and r.registry_digest='${v17Seal.digest}')`,
    `or (r.registry_version='scac-mutation-registry.v17' and r.registry_digest='${v17Seal.digest}')\n         or (r.registry_version='scac-mutation-registry.v18' and r.registry_digest='sha256:${v18Digest}')`,
    "Sourced shape forward-correction epoch-chain digest cases");
  sql = replaceExactlyOnce(sql,
    `  (registry_version='scac-mutation-registry.v18' and registry_digest='${v17Seal.digest}')`,
    `  (registry_version='scac-mutation-registry.v17' and registry_digest='${v17Seal.digest}') or\n  (registry_version='scac-mutation-registry.v18' and registry_digest='sha256:${v18Digest}')`,
    "Sourced shape forward-correction epoch constraint digest cases");
  sql = replaceExactlyOnce(sql,
    `'{registry_digest}',to_jsonb('${v17Seal.digest}'::text)`,
    `'{registry_digest}',to_jsonb('sha256:${v18Digest}'::text)`,
    "Sourced shape forward-correction snapshot registry digest");
  sql = replaceExactlyOnce(sql,
    "ops.scac_mutation_registry_v16_seal_available(),ops.scac_mutation_catalog_v18_current()",
    "ops.scac_mutation_registry_v16_seal_available(),ops.scac_mutation_catalog_v17_live_at_seal(),ops.scac_mutation_catalog_v17_current(),ops.scac_mutation_registry_v17_seal_available(),ops.scac_mutation_catalog_v18_current()",
    "Sourced shape forward-correction historical function revoke list");
  sql = replaceExactlyOnce(sql,
    "Backup guard/status successor snapshot: current policy epochs bind mutation registry v18 while historical v2/v3/v4/v5/v6/v7/v8/v9/v10/v11/v12/v13/v14/v15/v16 epochs remain immutable.",
    "Sourced shape forward-correction successor snapshot: current policy epochs bind mutation registry v18 while historical v2/v3/v4/v5/v6/v7/v8/v9/v10/v11/v12/v13/v14/v15/v16/v17 epochs remain immutable.",
    "Sourced shape forward-correction policy snapshot comment");
  sql = replaceExactlyOnce(sql,
    `(select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v18')<>${v17Seal.entryCount}`,
    `(select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v18')<>${entryCount}`,
    "Sourced shape forward-correction v18 entry count guard");
  sql = replaceExactlyOnce(sql,
    `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v16')<>'${HISTORICAL_REGISTRY_SEALS.v16.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v16')<>${HISTORICAL_REGISTRY_SEALS.v16.entryCount} then raise exception 'sealed SCAC mutation registry v16 changed during successor creation'; end if;`,
    `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v17')<>'${v17Seal.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v17')<>${v17Seal.entryCount} then raise exception 'sealed SCAC mutation registry v17 changed during successor creation'; end if;`,
    "Sourced shape forward-correction predecessor seal guard");
  sql = replaceExactlyOnce(sql,
    "ops.scac_policy_epoch_snapshot(),ops.scac_policy_epoch_snapshot_v6(),ops.scac_policy_epoch_snapshot_v7(),ops.scac_policy_epoch_snapshot_v8(),ops.scac_policy_epoch_snapshot_v9(),ops.scac_policy_epoch_snapshot_v10(),ops.scac_policy_epoch_snapshot_v11(),ops.scac_policy_epoch_snapshot_v12(),ops.scac_policy_epoch_snapshot_v13(),ops.scac_policy_epoch_snapshot_v14(),ops.scac_policy_epoch_snapshot_v15(),ops.scac_policy_epoch_snapshot_v16(),",
    "ops.scac_policy_epoch_snapshot(),ops.scac_policy_epoch_snapshot_v6(),ops.scac_policy_epoch_snapshot_v7(),ops.scac_policy_epoch_snapshot_v8(),ops.scac_policy_epoch_snapshot_v9(),ops.scac_policy_epoch_snapshot_v10(),ops.scac_policy_epoch_snapshot_v11(),ops.scac_policy_epoch_snapshot_v12(),ops.scac_policy_epoch_snapshot_v13(),ops.scac_policy_epoch_snapshot_v14(),ops.scac_policy_epoch_snapshot_v15(),ops.scac_policy_epoch_snapshot_v16(),ops.scac_policy_epoch_snapshot_v17(),",
    "Sourced shape forward-correction historical policy snapshot revoke list");

  const seedStartMarker = "with seed as (select value as contract from jsonb_array_elements(";
  const seedEndMarker = "::jsonb))\ninsert into ops.scac_mutation_registry_entry";
  const seedStart = sql.indexOf(seedStartMarker);
  const secondSeedStart = sql.indexOf(seedStartMarker, seedStart + seedStartMarker.length);
  const seedEnd = sql.indexOf(seedEndMarker, seedStart + seedStartMarker.length);
  const secondSeedEnd = sql.indexOf(seedEndMarker, seedEnd + seedEndMarker.length);
  if (seedStart < 0 || secondSeedStart >= 0 || seedEnd < 0 || secondSeedEnd >= 0)
    throw new Error("sealed SCAC v17 migration has no exact source-seed boundary");
  const seed = JSON.stringify(rows.map(row => ({ ...row, entry_digest: `sha256:${sha256(row)}` })));
  sql = `${sql.slice(0, seedStart + seedStartMarker.length)}${sqlLiteral(seed)}${sql.slice(seedEnd)}`;

  const preflightCurrent = renderV18Current(SOURCED_SHAPE_FORWARD_CORRECTION_PRE_V18_DB_CATALOG_BASELINE);
  const preflightBegin = preflightCurrent.indexOf("begin\n");
  const preflightEnd = preflightCurrent.lastIndexOf("end $fn$;");
  if (preflightBegin < 0 || preflightEnd <= preflightBegin)
    throw new Error("generated v18 catalog predicate has no exact preflight body boundary");
  let preflightBody = preflightCurrent.slice(preflightBegin + "begin\n".length, preflightEnd);
  preflightBody = preflightBody.replaceAll(
    "then return false; end if;",
    "then raise exception 'Sourced shape forward-correction pre-v18 catalog receipt drifted'; end if;");
  preflightBody = replaceExactlyOnce(preflightBody,
    `return observed_count=${SOURCED_SHAPE_FORWARD_CORRECTION_PRE_V18_DB_CATALOG_BASELINE.role_authority.count} and observed_digest='${SOURCED_SHAPE_FORWARD_CORRECTION_PRE_V18_DB_CATALOG_BASELINE.role_authority.digest}';`,
    `if observed_count<>${SOURCED_SHAPE_FORWARD_CORRECTION_PRE_V18_DB_CATALOG_BASELINE.role_authority.count} or observed_digest<>'${SOURCED_SHAPE_FORWARD_CORRECTION_PRE_V18_DB_CATALOG_BASELINE.role_authority.digest}' then raise exception 'Sourced shape forward-correction pre-v18 role-authority receipt drifted'; end if;`,
    "Sourced shape forward-correction pre-v18 role receipt");
  const predecessorHash = sha256(v17Migration);
  const predecessorPreflight =
`-- Exact disposable-Postgres post-0491 receipt. Refuse before the WR-000068
-- surface or any v18 function exists; the domain SQL below then changes the
-- catalog and the v18 successor seals the resulting catalog in one transaction.
do $sourced_shape_forward_correction_preflight$
declare observed_count integer; observed_digest text; grant_snapshot jsonb;
begin
  if (select count(*) from public.schema_migrations where filename='0491_backup_guard_status_registry_activation.sql')<>1
     or not exists(select 1 from public.schema_migrations where filename='0491_backup_guard_status_registry_activation.sql'
       and sha256='${predecessorHash}') then
    raise exception 'Sourced shape forward-correction pre-v18 migration ledger receipt drifted';
  end if;
  grant_snapshot:=ops.scac_runtime_dml_grant_snapshot();
  if (grant_snapshot->>'entry_count')::integer<>${SOURCED_SHAPE_FORWARD_CORRECTION_PRE_V18_DB_CATALOG_BASELINE.runtime_dml_grants.count}
     or grant_snapshot->>'grant_digest'<>'${SOURCED_SHAPE_FORWARD_CORRECTION_PRE_V18_DB_CATALOG_BASELINE.runtime_dml_grants.digest}' then
    raise exception 'Sourced shape forward-correction pre-v18 runtime grant receipt drifted';
  end if;
${preflightBody}end $sourced_shape_forward_correction_preflight$;

`;
  const domainSql = renderSourcedShapeForwardCorrectionDomainSql(predecessorArtifacts?.witnesses ?? {});
  return `${predecessorPreflight}${domainSql}${sql}`.replace(/\n+$/, "\n");
}


// ── WR-000069: contextual incident/work-request evidence ────────────────────
export function renderIncidentWorkRequestLinkDomainSql(witnesses = {}) {
  const witnessPath = "migrations/0426_withdraw_a_work_request_captured_in_error.sql";
  const source = witnesses[witnessPath] ?? readFileSync(resolve(REPO_ROOT, witnessPath), "utf8");
  if (sha256(source) !== SOURCED_SHAPE_FORWARD_CORRECTION_WITNESS_SHA256[witnessPath])
    throw new Error(`incident/work-request card witness changed: ${witnessPath}`);
  let card = sliceFunctionDefinition(source,
    "CREATE FUNCTION ops.work_request_card(", "0426 work_request_card");
  card = replaceExactlyOnce(card,
    "closed_at timestamp with time zone, superseded_by_ref text)",
    "closed_at timestamp with time zone, superseded_by_ref text, incident_evidence jsonb)",
    "WR69 card return shape");
  card = replaceExactlyOnce(card,
    "w.exit_reason,w.closed_at,succ.ref",
    "w.exit_reason,w.closed_at,succ.ref,coalesce(incident_evidence.items,'[]'::jsonb)",
    "WR69 card incident evidence projection");
  card = replaceExactlyOnce(card,
`    ) counted on true
   where`,
`    ) counted on true
    left join lateral (
      select jsonb_agg(to_jsonb(projected) order by projected.detected_at,projected.incident_ref) as items
        from (
          select i.ref as incident_ref,i.title,i.state,i.severity,i.environment,
                 to_jsonb(i.detected_at)#>>'{}' as detected_at,
                 to_jsonb(i.observed_at)#>>'{}' as observed_at,
                 to_jsonb(i.resolved_at)#>>'{}' as resolved_at,
                 occurrence.occurrences,occurrence.occurrence_evidence_status,
                 occurrence.legacy_overlap_unknown,occurrence.unresolved_occurrence_edge_count,
                 jsonb_build_object('kind','work_request','ref',w.ref) as association,
                 coalesce(evidence.items,'[]'::jsonb) as evidence
            from ops.incident_link anchor
            join ops.incident i on i.id=anchor.incident_id
            left join lateral (
              with occurrence_links as (
                select l.kind,l.ref,
                       case when l.ref ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
                            then l.ref::uuid else null::uuid end as target_uuid
                  from ops.incident_link l
                 where l.incident_id=i.id and l.kind in ('run','deployment')
              ), resolved_links as (
                select x.kind,x.ref,
                       case when x.kind='run' then
                              (select r.correlation_id::text from ops.run r where r.id=x.target_uuid)
                            when x.kind='deployment' then
                              (select dep.correlation_id::text from ops.deployment dep where dep.id=x.target_uuid)
                       end as resolved_correlation
                  from occurrence_links x
              ), correlation_suffixes as (
                select distinct substring(f.source_ref from 13) as correlation_suffix
                  from ops.incident_fact f
                 where f.incident_id=i.id and f.source_ref like 'correlation:%'
              ), occurrence_counts as (
                select (select count(*) from resolved_links)::int as link_count,
                       (select count(*) from correlation_suffixes)::int as correlation_count,
                       (select count(*) from resolved_links where resolved_correlation is null)::int as unresolved_count,
                       (select count(*) from correlation_suffixes c where not exists (
                          select 1 from resolved_links r where r.resolved_correlation=c.correlation_suffix
                       ))::int as unpaired_correlation_count
              )
              select case when unresolved_count=0
                          then greatest(1,link_count+unpaired_correlation_count)
                          else greatest(1,link_count,correlation_count) end::int as occurrences,
                     case when unresolved_count>0 then 'legacy_overlap_unknown' else 'complete' end as occurrence_evidence_status,
                     (unresolved_count>0) as legacy_overlap_unknown,
                     unresolved_count as unresolved_occurrence_edge_count
                from occurrence_counts
            ) occurrence on true
            left join lateral (
              select jsonb_agg(item order by item->>'occurred_at' nulls last,item->>'kind',
                                            coalesce(item->>'ref',item->>'source_ref')) as items
                from (
                  select jsonb_build_object('evidence_type','link','kind',l.kind,'ref',l.ref,
                                            'occurred_at',null) as item
                    from ops.incident_link l
                   where l.incident_id=i.id and l.kind in ('run','deployment')
                  union all
                  select jsonb_build_object('evidence_type','fact','kind','fact','text',f.text,
                                            'source_ref',f.source_ref,
                                            'recorded_at',to_jsonb(f.recorded_at)#>>'{}',
                                            'occurred_at',to_jsonb(f.recorded_at)#>>'{}') as item
                    from ops.incident_fact f
                   where f.incident_id=i.id and f.source_ref is not null
                  union all
                  select jsonb_build_object('evidence_type','trace','kind',t.kind,'ref',t.ref,
                                            'correlation_id',t.correlation_id,'state',t.state,
                                            'environment',t.environment,'service_key',t.service_key,
                                            'failure_class',t.failure_class,'detail',t.detail,
                                            'source_kind',t.source_kind,'source_ref',t.source_ref,
                                            'freshness_state',t.freshness_state,
                                            'occurred_at',to_jsonb(t.occurred_at)#>>'{}') as item
                    from ops.v_trace t
                   where t.correlation_id=i.correlation_id
                      or t.correlation_id in (
                        select substring(f.source_ref from 13)::uuid
                          from ops.incident_fact f
                         where f.incident_id=i.id
                           and f.source_ref ~ '^correlation:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
                      )
                ) evidence_rows
            ) evidence on true
           where anchor.kind='work_request' and anchor.ref=w.ref
        ) projected
    ) incident_evidence on true
   where`,
    "WR69 card bounded incident evidence aggregate");
  return `-- WR-000069: bound the per-incident fact lookup used by every occurrence consumer.\n` +
    `create index if not exists incident_fact_incident_source_idx\n` +
    `  on ops.incident_fact (incident_id,source_ref);\n\n` +
    `-- Add the contextual incident evidence projection to the existing safe card.\n` +
    `drop function ops.work_request_card(text,text);\n\n${card}\n` +
    "grant execute on function ops.work_request_card(text,text) to carr_reader,carr_writer;\n\n";
}
export function renderIncidentWorkRequestLinkRegistrySql(rows = fullInventory(),
  dbCatalogBaseline = INCIDENT_WORK_REQUEST_LINK_FORWARD_DB_CATALOG_BASELINE,
  predecessorArtifacts = undefined) {
  const { v18: v18Seal } = HISTORICAL_REGISTRY_SEALS;
  const v19Digest = registryDigestFor(REGISTRY_V19_VERSION, rows, dbCatalogBaseline);
  const catalogCount = dbCatalogBaseline.secdef_execute.count +
    dbCatalogBaseline.relation_dml.count + dbCatalogBaseline.column_dml.count;
  const entryCount = rows.length + catalogCount;
  const v18MigrationPath = "migrations/0492_sourced_shape_forward_correction_and_scac_successor.sql";
  const v18RuntimePath = "mcp-server/src/scac-mutation-registry.v18.generated.js";
  const v18Rows = frozenInventory(REGISTRY_V18_VERSION);
  const v18Migration = predecessorArtifacts?.migration ??
    renderSourcedShapeForwardCorrectionRegistrySql(
      v18Rows, SOURCED_SHAPE_FORWARD_CORRECTION_FORWARD_DB_CATALOG_BASELINE);
  const v18Runtime = predecessorArtifacts?.runtime ?? renderRuntimeProjection(v18Rows, {
    version: REGISTRY_V18_VERSION,
    dbCatalogBaseline: SOURCED_SHAPE_FORWARD_CORRECTION_FORWARD_DB_CATALOG_BASELINE,
  });
  for (const [path, source] of [
    [v18MigrationPath, v18Migration], [v18RuntimePath, v18Runtime],
  ]) {
    const observed = sha256(source);
    if (observed !== HISTORICAL_REGISTRY_ARTIFACT_SHA256[path])
      throw new Error(`sealed historical SCAC v18 artifact changed: ${path}: ${observed}`);
  }

  const headerMarker =
    "-- SCAC-12: forward-only mutation registry v18 after sourced shape forward correction.";
  const coreStart = v18Migration.indexOf(headerMarker);
  if (coreStart < 0 || v18Migration.indexOf(headerMarker, coreStart + headerMarker.length) >= 0)
    throw new Error("sealed SCAC v18 migration has no exact successor core boundary");
  const v18Core = v18Migration.slice(coreStart);
  const currentV18Marker = "create or replace function ops.scac_mutation_catalog_v18_current()";
  const policyMarker =
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v17;";
  const currentV18Start = v18Core.indexOf(currentV18Marker);
  const secondCurrentV18 = v18Core.indexOf(
    currentV18Marker, currentV18Start + currentV18Marker.length);
  const v17HistoryMarker =
    "alter function ops.scac_mutation_catalog_v17_current() rename to scac_mutation_catalog_v17_live_at_seal;";
  const v17HistoryStart = v18Core.indexOf(v17HistoryMarker);
  const secondV17History = v18Core.indexOf(
    v17HistoryMarker, v17HistoryStart + v17HistoryMarker.length);
  const policyStart = v18Core.indexOf(policyMarker);
  const secondPolicy = v18Core.indexOf(policyMarker, policyStart + policyMarker.length);
  if (v17HistoryStart < 0 || secondV17History >= 0 || currentV18Start <= v17HistoryStart ||
      secondCurrentV18 >= 0 || policyStart <= currentV18Start || secondPolicy >= 0)
    throw new Error("sealed SCAC v18 migration has no exact catalog successor boundary");
  const installedV17History = v18Core.slice(v17HistoryStart, currentV18Start);
  const v18Current = v18Core.slice(currentV18Start, policyStart);
  const v18History =
`alter function ops.scac_mutation_catalog_v18_current() rename to scac_mutation_catalog_v18_live_at_seal;
create or replace function ops.scac_mutation_registry_v18_seal_available()
returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$
  select ops.scac_mutation_registry_seal_valid('scac-mutation-registry.v18')
$fn$;
create or replace function ops.scac_mutation_catalog_v18_current()
returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$
  select ops.scac_mutation_catalog_v18_live_at_seal()
$fn$;
comment on function ops.scac_mutation_registry_v18_seal_available() is 'Exact immutable v18 registry seal; separate from whether the live catalog still equals v18.';
comment on function ops.scac_mutation_catalog_v18_current() is 'Historical v18 live-catalog validator; expected to become false after the v19 authority surface is installed.';

`;
  const renderV19Current = baseline => {
    let current = v18Current
      .replaceAll("scac_mutation_catalog_v18_current", "scac_mutation_catalog_v19_current")
      .replaceAll("scac-mutation-registry.v18", "scac-mutation-registry.v19");
    current = replaceExactlyOnce(current,
      `if observed_count<>${SOURCED_SHAPE_FORWARD_CORRECTION_FORWARD_DB_CATALOG_BASELINE.secdef_execute.count} or observed_digest<>'${SOURCED_SHAPE_FORWARD_CORRECTION_FORWARD_DB_CATALOG_BASELINE.secdef_execute.digest}' then return false; end if;`,
      `if observed_count<>${baseline.secdef_execute.count} or observed_digest<>'${baseline.secdef_execute.digest}' then return false; end if;`,
      "Incident work-request link v19 security-definer baseline");
    current = replaceExactlyOnce(current,
      `if observed_count<>${SOURCED_SHAPE_FORWARD_CORRECTION_FORWARD_DB_CATALOG_BASELINE.relation_dml.count} or observed_digest<>'${SOURCED_SHAPE_FORWARD_CORRECTION_FORWARD_DB_CATALOG_BASELINE.relation_dml.digest}' then return false; end if;`,
      `if observed_count<>${baseline.relation_dml.count} or observed_digest<>'${baseline.relation_dml.digest}' then return false; end if;`,
      "Incident work-request link v19 relation baseline");
    current = replaceExactlyOnce(current,
      `if observed_count<>${SOURCED_SHAPE_FORWARD_CORRECTION_FORWARD_DB_CATALOG_BASELINE.column_dml.count} or observed_digest<>'${SOURCED_SHAPE_FORWARD_CORRECTION_FORWARD_DB_CATALOG_BASELINE.column_dml.digest}' then return false; end if;`,
      `if observed_count<>${baseline.column_dml.count} or observed_digest<>'${baseline.column_dml.digest}' then return false; end if;`,
      "Incident work-request link v19 column baseline");
    return replaceExactlyOnce(current,
      `return observed_count=${SOURCED_SHAPE_FORWARD_CORRECTION_FORWARD_DB_CATALOG_BASELINE.role_authority.count} and observed_digest='${SOURCED_SHAPE_FORWARD_CORRECTION_FORWARD_DB_CATALOG_BASELINE.role_authority.digest}';`,
      `return observed_count=${baseline.role_authority.count} and observed_digest='${baseline.role_authority.digest}';`,
      "Incident work-request link v19 role-authority baseline");
  };
  const v19Current = renderV19Current(dbCatalogBaseline);

  let sql = replaceExactlyOnce(v18Core, v18Current,
    "__SOURCED_SHAPE_V18_CATALOG_SUCCESSOR__",
    "Incident work-request link v18 current catalog block");
  sql = replaceExactlyOnce(sql, installedV17History, "",
    "Incident work-request link already-installed v17 catalog history");
  sql = replaceExactlyOnce(sql, headerMarker,
    "-- SCAC-12: forward-only mutation registry v19 after incident work-request link.",
    "Incident work-request link migration header");
  sql = sql
    .replaceAll("scac-mutation-registry.v18", "scac-mutation-registry.v19")
    .replaceAll("_v18", "_v19")
    .replaceAll(" v18", " v19");
  sql = replaceExactlyOnce(sql, JSON.stringify(SOURCED_SHAPE_FORWARD_CORRECTION_FORWARD_DB_CATALOG_BASELINE),
    JSON.stringify(dbCatalogBaseline), "Incident work-request link v19 catalog projection");
  sql = replaceExactlyOnce(sql,
    `'${v18Seal.digest}',${v18Seal.entryCount},${v18Seal.sourceEntryCount},`,
    `'sha256:${v19Digest}',${entryCount},${rows.length},`,
    "Incident work-request link v19 registry row");
  sql = replaceExactlyOnce(sql,
    `ops.scac_mutation_registration_v19('${v18Seal.digest}',`,
    `ops.scac_mutation_registration_v19('sha256:${v19Digest}',`,
    "Incident work-request link v19 snapshot registry lookup");
  sql = replaceExactlyOnce(sql,
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v17;",
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v18;",
    "Incident work-request link policy snapshot predecessor");
  sql = replaceExactlyOnce(sql, "__SOURCED_SHAPE_V18_CATALOG_SUCCESSOR__",
    `${v18History}${v19Current}`, "Incident work-request link v18 catalog history insertion");

  const versionsThrough18 = Array.from({ length: 18 }, (_, index) =>
    `'scac-mutation-registry.v${index + 1}'`).join(",");
  const versionsThrough17 = Array.from({ length: 17 }, (_, index) =>
    `'scac-mutation-registry.v${index + 1}'`).join(",");
  sql = replaceExactlyOnce(sql,
    `check (registry_version in (${versionsThrough17},'scac-mutation-registry.v19'))`,
    `check (registry_version in (${versionsThrough18},'scac-mutation-registry.v19'))`,
    "Incident work-request link registry-version constraint");
  sql = replaceExactlyOnce(sql,
    `if p_registry_version not in (${versionsThrough17}) then return false; end if;`,
    `if p_registry_version not in (${versionsThrough18}) then return false; end if;`,
    "Incident work-request link historical seal allowlist");
  sql = replaceExactlyOnce(sql,
    `    when 'scac-mutation-registry.v17' then '${HISTORICAL_REGISTRY_SEALS.v17.digest}' end;`,
    `    when 'scac-mutation-registry.v17' then '${HISTORICAL_REGISTRY_SEALS.v17.digest}'\n    when '${v18Seal.version}' then '${v18Seal.digest}' end;`,
    "Incident work-request link historical digest case");
  sql = replaceExactlyOnce(sql,
    `    when 'scac-mutation-registry.v17' then '${JSON.stringify(BACKUP_GUARD_STATUS_FORWARD_DB_CATALOG_BASELINE)}'::jsonb end;`,
    `    when 'scac-mutation-registry.v17' then '${JSON.stringify(BACKUP_GUARD_STATUS_FORWARD_DB_CATALOG_BASELINE)}'::jsonb\n    when '${v18Seal.version}' then '${JSON.stringify(SOURCED_SHAPE_FORWARD_CORRECTION_FORWARD_DB_CATALOG_BASELINE)}'::jsonb end;`,
    "Incident work-request link historical catalog case");
  sql = replaceExactlyOnce(sql,
    `    ('scac-mutation-registry.v17','${HISTORICAL_REGISTRY_SEALS.v17.digest}',${HISTORICAL_REGISTRY_SEALS.v17.entryCount},${HISTORICAL_REGISTRY_SEALS.v17.sourceEntryCount})\n`,
    `    ('scac-mutation-registry.v17','${HISTORICAL_REGISTRY_SEALS.v17.digest}',${HISTORICAL_REGISTRY_SEALS.v17.entryCount},${HISTORICAL_REGISTRY_SEALS.v17.sourceEntryCount}),\n    ('${v18Seal.version}','${v18Seal.digest}',${v18Seal.entryCount},${v18Seal.sourceEntryCount})\n`,
    "Incident work-request link historical seal tuple");
  sql = replaceExactlyOnce(sql,
    "    ops.scac_mutation_registry_v17_seal_available()) then",
    "    ops.scac_mutation_registry_v17_seal_available() and\n    ops.scac_mutation_registry_v18_seal_available()) then",
    "Incident work-request link snapshot predecessor seal");
  sql = replaceExactlyOnce(sql,
    `or (r.registry_version='scac-mutation-registry.v19' and r.registry_digest='${v18Seal.digest}')`,
    `or (r.registry_version='scac-mutation-registry.v18' and r.registry_digest='${v18Seal.digest}')\n         or (r.registry_version='scac-mutation-registry.v19' and r.registry_digest='sha256:${v19Digest}')`,
    "Incident work-request link epoch-chain digest cases");
  sql = replaceExactlyOnce(sql,
    `  (registry_version='scac-mutation-registry.v19' and registry_digest='${v18Seal.digest}')`,
    `  (registry_version='scac-mutation-registry.v18' and registry_digest='${v18Seal.digest}') or\n  (registry_version='scac-mutation-registry.v19' and registry_digest='sha256:${v19Digest}')`,
    "Incident work-request link epoch constraint digest cases");
  sql = replaceExactlyOnce(sql,
    `'{registry_digest}',to_jsonb('${v18Seal.digest}'::text)`,
    `'{registry_digest}',to_jsonb('sha256:${v19Digest}'::text)`,
    "Incident work-request link snapshot registry digest");
  sql = replaceExactlyOnce(sql,
    "ops.scac_mutation_registry_v17_seal_available(),ops.scac_mutation_catalog_v19_current()",
    "ops.scac_mutation_registry_v17_seal_available(),ops.scac_mutation_catalog_v18_live_at_seal(),ops.scac_mutation_catalog_v18_current(),ops.scac_mutation_registry_v18_seal_available(),ops.scac_mutation_catalog_v19_current()",
    "Incident work-request link historical function revoke list");
  sql = replaceExactlyOnce(sql,
    "Sourced shape forward-correction successor snapshot: current policy epochs bind mutation registry v19 while historical v2/v3/v4/v5/v6/v7/v8/v9/v10/v11/v12/v13/v14/v15/v16/v17 epochs remain immutable.",
    "Incident work-request link successor snapshot: current policy epochs bind mutation registry v19 while historical v2/v3/v4/v5/v6/v7/v8/v9/v10/v11/v12/v13/v14/v15/v16/v17/v18 epochs remain immutable.",
    "Incident work-request link policy snapshot comment");
  sql = replaceExactlyOnce(sql,
    `(select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v19')<>${v18Seal.entryCount}`,
    `(select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v19')<>${entryCount}`,
    "Incident work-request link v19 entry count guard");
  sql = replaceExactlyOnce(sql,
    `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v17')<>'${HISTORICAL_REGISTRY_SEALS.v17.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v17')<>${HISTORICAL_REGISTRY_SEALS.v17.entryCount} then raise exception 'sealed SCAC mutation registry v17 changed during successor creation'; end if;`,
    `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v18')<>'${v18Seal.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v18')<>${v18Seal.entryCount} then raise exception 'sealed SCAC mutation registry v18 changed during successor creation'; end if;`,
    "Incident work-request link predecessor seal guard");
  sql = replaceExactlyOnce(sql,
    "ops.scac_policy_epoch_snapshot(),ops.scac_policy_epoch_snapshot_v6(),ops.scac_policy_epoch_snapshot_v7(),ops.scac_policy_epoch_snapshot_v8(),ops.scac_policy_epoch_snapshot_v9(),ops.scac_policy_epoch_snapshot_v10(),ops.scac_policy_epoch_snapshot_v11(),ops.scac_policy_epoch_snapshot_v12(),ops.scac_policy_epoch_snapshot_v13(),ops.scac_policy_epoch_snapshot_v14(),ops.scac_policy_epoch_snapshot_v15(),ops.scac_policy_epoch_snapshot_v16(),ops.scac_policy_epoch_snapshot_v17(),",
    "ops.scac_policy_epoch_snapshot(),ops.scac_policy_epoch_snapshot_v6(),ops.scac_policy_epoch_snapshot_v7(),ops.scac_policy_epoch_snapshot_v8(),ops.scac_policy_epoch_snapshot_v9(),ops.scac_policy_epoch_snapshot_v10(),ops.scac_policy_epoch_snapshot_v11(),ops.scac_policy_epoch_snapshot_v12(),ops.scac_policy_epoch_snapshot_v13(),ops.scac_policy_epoch_snapshot_v14(),ops.scac_policy_epoch_snapshot_v15(),ops.scac_policy_epoch_snapshot_v16(),ops.scac_policy_epoch_snapshot_v17(),ops.scac_policy_epoch_snapshot_v18(),",
    "Incident work-request link historical policy snapshot revoke list");

  const seedStartMarker = "with seed as (select value as contract from jsonb_array_elements(";
  const seedEndMarker = "::jsonb))\ninsert into ops.scac_mutation_registry_entry";
  const seedStart = sql.indexOf(seedStartMarker);
  const secondSeedStart = sql.indexOf(seedStartMarker, seedStart + seedStartMarker.length);
  const seedEnd = sql.indexOf(seedEndMarker, seedStart + seedStartMarker.length);
  const secondSeedEnd = sql.indexOf(seedEndMarker, seedEnd + seedEndMarker.length);
  if (seedStart < 0 || secondSeedStart >= 0 || seedEnd < 0 || secondSeedEnd >= 0)
    throw new Error("sealed SCAC v18 migration has no exact source-seed boundary");
  const seed = JSON.stringify(rows.map(row => ({ ...row, entry_digest: `sha256:${sha256(row)}` })));
  sql = `${sql.slice(0, seedStart + seedStartMarker.length)}${sqlLiteral(seed)}${sql.slice(seedEnd)}`;

  const preflightCurrent = renderV19Current(SOURCED_SHAPE_FORWARD_CORRECTION_FORWARD_DB_CATALOG_BASELINE);
  const preflightBegin = preflightCurrent.indexOf("begin\n");
  const preflightEnd = preflightCurrent.lastIndexOf("end $fn$;");
  if (preflightBegin < 0 || preflightEnd <= preflightBegin)
    throw new Error("generated v19 catalog predicate has no exact preflight body boundary");
  let preflightBody = preflightCurrent.slice(preflightBegin + "begin\n".length, preflightEnd);
  preflightBody = preflightBody.replaceAll(
    "then return false; end if;",
    "then raise exception 'Incident work-request link pre-v19 catalog receipt drifted'; end if;");
  preflightBody = replaceExactlyOnce(preflightBody,
    `return observed_count=${SOURCED_SHAPE_FORWARD_CORRECTION_FORWARD_DB_CATALOG_BASELINE.role_authority.count} and observed_digest='${SOURCED_SHAPE_FORWARD_CORRECTION_FORWARD_DB_CATALOG_BASELINE.role_authority.digest}';`,
    `if observed_count<>${SOURCED_SHAPE_FORWARD_CORRECTION_FORWARD_DB_CATALOG_BASELINE.role_authority.count} or observed_digest<>'${SOURCED_SHAPE_FORWARD_CORRECTION_FORWARD_DB_CATALOG_BASELINE.role_authority.digest}' then raise exception 'Incident work-request link pre-v19 role-authority receipt drifted'; end if;`,
    "Incident work-request link pre-v19 role receipt");
  const predecessorHash = sha256(v18Migration);
  const predecessorPreflight =
`-- Exact disposable-Postgres post-0492 receipt. Refuse before the WR-000069
-- incident/work-request surface or any v19 function exists; the domain SQL then changes the
-- catalog and the v19 successor seals the resulting catalog in one transaction.
do $incident_work_request_link_preflight$
declare observed_count integer; observed_digest text; grant_snapshot jsonb;
begin
  if (select count(*) from public.schema_migrations where filename='0492_sourced_shape_forward_correction_and_scac_successor.sql')<>1
     or not exists(select 1 from public.schema_migrations where filename='0492_sourced_shape_forward_correction_and_scac_successor.sql'
       and sha256='${predecessorHash}') then
    raise exception 'Incident work-request link pre-v19 migration ledger receipt drifted';
  end if;
  grant_snapshot:=ops.scac_runtime_dml_grant_snapshot();
  if (grant_snapshot->>'entry_count')::integer<>${SOURCED_SHAPE_FORWARD_CORRECTION_FORWARD_DB_CATALOG_BASELINE.runtime_dml_grants.count}
     or grant_snapshot->>'grant_digest'<>'${SOURCED_SHAPE_FORWARD_CORRECTION_FORWARD_DB_CATALOG_BASELINE.runtime_dml_grants.digest}' then
    raise exception 'Incident work-request link pre-v19 runtime grant receipt drifted';
  end if;
${preflightBody}end $incident_work_request_link_preflight$;

`;
  const domainSql = renderIncidentWorkRequestLinkDomainSql(predecessorArtifacts?.witnesses ?? {});
  return `${predecessorPreflight}${domainSql}${sql}`.replace(/\n+$/, "\n");
}

// Shared v20 trust root. Every entry path that renders or writes a v20
// artifact calls this BEFORE doing work, so an unbound template constant can
// never reach a digest, a projection or a written file. It reads only fixed
// module constants: there is deliberately no parameter through which a caller
// could supply its own expected seal, pin or baseline.
const CONTINUITY_ARCHIVE_V19_MIGRATION_PATH =
  "migrations/0493_incident_work_request_link_scac_successor.sql";
const CONTINUITY_ARCHIVE_V19_RUNTIME_PATH =
  "mcp-server/src/scac-mutation-registry.v19.generated.js";
const CONTINUITY_ARCHIVE_DIGEST_RE = /^sha256:[0-9a-f]{64}$/;
const CONTINUITY_ARCHIVE_ARTIFACT_SHA_RE = /^[0-9a-f]{64}$/;

function assertContinuityArchiveCatalogBaseline(label, baseline, projectionVersion) {
  if (!baseline || baseline.projection_version !== projectionVersion)
    throw new Error(
      `continuity archive ${label} catalog baseline is not ${projectionVersion}`);
  for (const category of [
    "secdef_execute", "relation_dml", "column_dml", "role_authority", "runtime_dml_grants",
  ]) {
    const receipt = baseline[category];
    if (!Number.isInteger(receipt?.count) || receipt.count < 0 ||
        !CONTINUITY_ARCHIVE_DIGEST_RE.test(receipt?.digest ?? ""))
      throw new Error(`continuity archive ${label} ${category} receipt is unbound`);
  }
}

export function assertContinuityArchiveV20TrustRoot() {
  const { v19: v19Seal } = HISTORICAL_REGISTRY_SEALS;
  if (v19Seal?.version !== REGISTRY_V19_VERSION ||
      !CONTINUITY_ARCHIVE_DIGEST_RE.test(v19Seal?.digest ?? "") ||
      !Number.isInteger(v19Seal?.entryCount) || v19Seal.entryCount < 1 ||
      !Number.isInteger(v19Seal?.sourceEntryCount) || v19Seal.sourceEntryCount < 1)
    throw new Error("continuity archive v20 predecessor seal is unbound");
  // The chain is exact in both directions: v19 is the only accepted predecessor
  // projection and v20 the only accepted successor, so the predecessor catalog
  // can never be handed back as the successor.
  assertContinuityArchiveCatalogBaseline("predecessor v19",
    CONTINUITY_ARCHIVE_PRE_V20_DB_CATALOG_BASELINE, "scac-db-catalog-projection.v19");
  assertContinuityArchiveCatalogBaseline("successor v20",
    CONTINUITY_ARCHIVE_FORWARD_DB_CATALOG_BASELINE, "scac-db-catalog-projection.v20");
  for (const path of [
    CONTINUITY_ARCHIVE_V19_MIGRATION_PATH, CONTINUITY_ARCHIVE_V19_RUNTIME_PATH,
  ]) {
    if (!CONTINUITY_ARCHIVE_ARTIFACT_SHA_RE.test(
      HISTORICAL_REGISTRY_ARTIFACT_SHA256[path] ?? ""))
      throw new Error(`continuity archive v20 predecessor artifact pin is unbound: ${path}`);
  }
}

// Registry-only successor for the Codex continuity archive contract. The
// source change extends an existing read tool, so this migration adds no domain
// DDL or business rows; it only seals the new source inventory and installs the
// v20 catalog/policy projection after the immutable v19 frontier.
export function renderContinuityArchiveForwardRegistrySql(rows = fullInventory(),
  dbCatalogBaseline = CONTINUITY_ARCHIVE_FORWARD_DB_CATALOG_BASELINE,
  predecessorArtifacts = undefined) {
  // The predecessor seal, its catalog projection and the sealed v19 artifact
  // hashes are fixed production constants, asserted by the shared v20 trust
  // root that every v20 entry path calls. There is deliberately no caller
  // binding for them: a supplied predecessor artifact is checked AGAINST these
  // pins, it never supplies its own expected hash.
  assertContinuityArchiveV20TrustRoot();
  const { v19: v19Seal } = HISTORICAL_REGISTRY_SEALS;
  const predecessorDbCatalogBaseline = CONTINUITY_ARCHIVE_PRE_V20_DB_CATALOG_BASELINE;
  const artifactShaRe = CONTINUITY_ARCHIVE_ARTIFACT_SHA_RE;
  // A caller-supplied successor baseline is held to the SAME exact-projection
  // shape as the fixed constant; it can only ever narrow, never widen.
  assertContinuityArchiveCatalogBaseline("successor v20", dbCatalogBaseline,
    "scac-db-catalog-projection.v20");

  const v20Digest = registryDigestFor(REGISTRY_V20_VERSION, rows, dbCatalogBaseline);
  const catalogCount = dbCatalogBaseline.secdef_execute.count +
    dbCatalogBaseline.relation_dml.count + dbCatalogBaseline.column_dml.count;
  const entryCount = rows.length + catalogCount;
  const v19MigrationPath = CONTINUITY_ARCHIVE_V19_MIGRATION_PATH;
  const v19RuntimePath = CONTINUITY_ARCHIVE_V19_RUNTIME_PATH;
  const v19Rows = frozenInventory(REGISTRY_V19_VERSION);
  // A partial predecessor bundle regenerates only the missing half, and it
  // regenerates it from the canonical prior inputs: the v19 renderer's third
  // argument is its own v18-shaped predecessor bundle, so this v19-shaped one
  // is never forwarded into it.
  const v19Migration = predecessorArtifacts?.migration ??
    renderIncidentWorkRequestLinkRegistrySql(v19Rows, predecessorDbCatalogBaseline);
  const v19Runtime = predecessorArtifacts?.runtime ?? renderRuntimeProjection(v19Rows, {
    version: REGISTRY_V19_VERSION,
    dbCatalogBaseline: predecessorDbCatalogBaseline,
  });
  for (const [path, source] of [
    [v19MigrationPath, v19Migration], [v19RuntimePath, v19Runtime],
  ]) {
    const expected = HISTORICAL_REGISTRY_ARTIFACT_SHA256[path];
    if (!artifactShaRe.test(expected ?? ""))
      throw new Error(`continuity archive v20 predecessor artifact pin is unbound: ${path}`);
    const observed = sha256(source);
    if (observed !== expected)
      throw new Error(`sealed historical SCAC v19 artifact changed: ${path}: ${observed}`);
  }

  const headerMarker =
    "-- SCAC-12: forward-only mutation registry v19 after incident work-request link.";
  const coreStart = v19Migration.indexOf(headerMarker);
  if (coreStart < 0 || v19Migration.indexOf(headerMarker, coreStart + headerMarker.length) >= 0)
    throw new Error("sealed SCAC v19 migration has no exact successor core boundary");
  const v19Core = v19Migration.slice(coreStart);
  const currentV19Marker = "create or replace function ops.scac_mutation_catalog_v19_current()";
  const policyMarker =
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v18;";
  const currentV19Start = v19Core.indexOf(currentV19Marker);
  const secondCurrentV19 = v19Core.indexOf(
    currentV19Marker, currentV19Start + currentV19Marker.length);
  const v18HistoryMarker =
    "alter function ops.scac_mutation_catalog_v18_current() rename to scac_mutation_catalog_v18_live_at_seal;";
  const v18HistoryStart = v19Core.indexOf(v18HistoryMarker);
  const secondV18History = v19Core.indexOf(
    v18HistoryMarker, v18HistoryStart + v18HistoryMarker.length);
  const policyStart = v19Core.indexOf(policyMarker);
  const secondPolicy = v19Core.indexOf(policyMarker, policyStart + policyMarker.length);
  if (v18HistoryStart < 0 || secondV18History >= 0 || currentV19Start <= v18HistoryStart ||
      secondCurrentV19 >= 0 || policyStart <= currentV19Start || secondPolicy >= 0)
    throw new Error("sealed SCAC v19 migration has no exact catalog successor boundary");
  const installedV18History = v19Core.slice(v18HistoryStart, currentV19Start);
  const v19Current = v19Core.slice(currentV19Start, policyStart);
  const v19History =
`alter function ops.scac_mutation_catalog_v19_current() rename to scac_mutation_catalog_v19_live_at_seal;
create or replace function ops.scac_mutation_registry_v19_seal_available()
returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$
  select ops.scac_mutation_registry_seal_valid('scac-mutation-registry.v19')
$fn$;
create or replace function ops.scac_mutation_catalog_v19_current()
returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$
  select ops.scac_mutation_catalog_v19_live_at_seal()
$fn$;
comment on function ops.scac_mutation_registry_v19_seal_available() is 'Exact immutable v19 registry seal; separate from whether the live catalog still equals v19.';
comment on function ops.scac_mutation_catalog_v19_current() is 'Historical v19 live-catalog validator; expected to become false after the v20 authority surface is installed.';

`;
  const renderV20Current = baseline => {
    let current = v19Current
      .replaceAll("scac_mutation_catalog_v19_current", "scac_mutation_catalog_v20_current")
      .replaceAll("scac-mutation-registry.v19", "scac-mutation-registry.v20");
    for (const [category, label] of [
      ["secdef_execute", "security-definer"],
      ["relation_dml", "relation"],
      ["column_dml", "column"],
    ]) {
      current = replaceExactlyOnce(current,
        `if observed_count<>${predecessorDbCatalogBaseline[category].count} or observed_digest<>'${predecessorDbCatalogBaseline[category].digest}' then return false; end if;`,
        `if observed_count<>${baseline[category].count} or observed_digest<>'${baseline[category].digest}' then return false; end if;`,
        `Continuity archive v20 ${label} baseline`);
    }
    return replaceExactlyOnce(current,
      `return observed_count=${predecessorDbCatalogBaseline.role_authority.count} and observed_digest='${predecessorDbCatalogBaseline.role_authority.digest}';`,
      `return observed_count=${baseline.role_authority.count} and observed_digest='${baseline.role_authority.digest}';`,
      "Continuity archive v20 role-authority baseline");
  };
  const v20Current = renderV20Current(dbCatalogBaseline);

  let sql = replaceExactlyOnce(v19Core, v19Current,
    "__INCIDENT_LINK_V19_CATALOG_SUCCESSOR__",
    "Continuity archive v19 current catalog block");
  sql = replaceExactlyOnce(sql, installedV18History, "",
    "Continuity archive already-installed v18 catalog history");
  sql = replaceExactlyOnce(sql, headerMarker,
    "-- SCAC-12: registry-only mutation registry v20 after Codex continuity archive recovery.",
    "Continuity archive migration header");
  sql = sql
    .replaceAll("scac-mutation-registry.v19", "scac-mutation-registry.v20")
    .replaceAll("_v19", "_v20")
    .replaceAll(" v19", " v20");
  sql = replaceExactlyOnce(sql, JSON.stringify(predecessorDbCatalogBaseline),
    JSON.stringify(dbCatalogBaseline), "Continuity archive v20 catalog projection");
  sql = replaceExactlyOnce(sql,
    `'${v19Seal.digest}',${v19Seal.entryCount},${v19Seal.sourceEntryCount},`,
    `'sha256:${v20Digest}',${entryCount},${rows.length},`,
    "Continuity archive v20 registry row");
  sql = replaceExactlyOnce(sql,
    `ops.scac_mutation_registration_v20('${v19Seal.digest}',`,
    `ops.scac_mutation_registration_v20('sha256:${v20Digest}',`,
    "Continuity archive v20 snapshot registry lookup");
  sql = replaceExactlyOnce(sql,
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v18;",
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v19;",
    "Continuity archive policy snapshot predecessor");
  sql = replaceExactlyOnce(sql, "__INCIDENT_LINK_V19_CATALOG_SUCCESSOR__",
    `${v19History}${v20Current}`, "Continuity archive v19 catalog history insertion");

  const versionsThrough19 = Array.from({ length: 19 }, (_, index) =>
    `'scac-mutation-registry.v${index + 1}'`).join(",");
  const versionsThrough18 = Array.from({ length: 18 }, (_, index) =>
    `'scac-mutation-registry.v${index + 1}'`).join(",");
  sql = replaceExactlyOnce(sql,
    `check (registry_version in (${versionsThrough18},'scac-mutation-registry.v20'))`,
    `check (registry_version in (${versionsThrough19},'scac-mutation-registry.v20'))`,
    "Continuity archive registry-version constraint");
  sql = replaceExactlyOnce(sql,
    `if p_registry_version not in (${versionsThrough18}) then return false; end if;`,
    `if p_registry_version not in (${versionsThrough19}) then return false; end if;`,
    "Continuity archive historical seal allowlist");
  sql = replaceExactlyOnce(sql,
    `    when 'scac-mutation-registry.v18' then '${HISTORICAL_REGISTRY_SEALS.v18.digest}' end;`,
    `    when 'scac-mutation-registry.v18' then '${HISTORICAL_REGISTRY_SEALS.v18.digest}'\n    when '${v19Seal.version}' then '${v19Seal.digest}' end;`,
    "Continuity archive historical digest case");
  sql = replaceExactlyOnce(sql,
    `    when 'scac-mutation-registry.v18' then '${JSON.stringify(SOURCED_SHAPE_FORWARD_CORRECTION_FORWARD_DB_CATALOG_BASELINE)}'::jsonb end;`,
    `    when 'scac-mutation-registry.v18' then '${JSON.stringify(SOURCED_SHAPE_FORWARD_CORRECTION_FORWARD_DB_CATALOG_BASELINE)}'::jsonb\n    when '${v19Seal.version}' then '${JSON.stringify(predecessorDbCatalogBaseline)}'::jsonb end;`,
    "Continuity archive historical catalog case");
  sql = replaceExactlyOnce(sql,
    `    ('scac-mutation-registry.v18','${HISTORICAL_REGISTRY_SEALS.v18.digest}',${HISTORICAL_REGISTRY_SEALS.v18.entryCount},${HISTORICAL_REGISTRY_SEALS.v18.sourceEntryCount})\n`,
    `    ('scac-mutation-registry.v18','${HISTORICAL_REGISTRY_SEALS.v18.digest}',${HISTORICAL_REGISTRY_SEALS.v18.entryCount},${HISTORICAL_REGISTRY_SEALS.v18.sourceEntryCount}),\n    ('${v19Seal.version}','${v19Seal.digest}',${v19Seal.entryCount},${v19Seal.sourceEntryCount})\n`,
    "Continuity archive historical seal tuple");
  sql = replaceExactlyOnce(sql,
    "    ops.scac_mutation_registry_v18_seal_available()) then",
    "    ops.scac_mutation_registry_v18_seal_available() and\n    ops.scac_mutation_registry_v19_seal_available()) then",
    "Continuity archive snapshot predecessor seal");
  sql = replaceExactlyOnce(sql,
    `or (r.registry_version='scac-mutation-registry.v20' and r.registry_digest='${v19Seal.digest}')`,
    `or (r.registry_version='scac-mutation-registry.v19' and r.registry_digest='${v19Seal.digest}')\n         or (r.registry_version='scac-mutation-registry.v20' and r.registry_digest='sha256:${v20Digest}')`,
    "Continuity archive epoch-chain digest cases");
  sql = replaceExactlyOnce(sql,
    `  (registry_version='scac-mutation-registry.v20' and registry_digest='${v19Seal.digest}')`,
    `  (registry_version='scac-mutation-registry.v19' and registry_digest='${v19Seal.digest}') or\n  (registry_version='scac-mutation-registry.v20' and registry_digest='sha256:${v20Digest}')`,
    "Continuity archive epoch constraint digest cases");
  sql = replaceExactlyOnce(sql,
    `'{registry_digest}',to_jsonb('${v19Seal.digest}'::text)`,
    `'{registry_digest}',to_jsonb('sha256:${v20Digest}'::text)`,
    "Continuity archive snapshot registry digest");
  sql = replaceExactlyOnce(sql,
    "ops.scac_mutation_registry_v18_seal_available(),ops.scac_mutation_catalog_v20_current()",
    "ops.scac_mutation_registry_v18_seal_available(),ops.scac_mutation_catalog_v19_live_at_seal(),ops.scac_mutation_catalog_v19_current(),ops.scac_mutation_registry_v19_seal_available(),ops.scac_mutation_catalog_v20_current()",
    "Continuity archive historical function revoke list");
  sql = replaceExactlyOnce(sql,
    "Incident work-request link successor snapshot: current policy epochs bind mutation registry v20 while historical v2/v3/v4/v5/v6/v7/v8/v9/v10/v11/v12/v13/v14/v15/v16/v17/v18 epochs remain immutable.",
    "Continuity archive successor snapshot: current policy epochs bind mutation registry v20 while historical v2/v3/v4/v5/v6/v7/v8/v9/v10/v11/v12/v13/v14/v15/v16/v17/v18/v19 epochs remain immutable.",
    "Continuity archive policy snapshot comment");
  sql = replaceExactlyOnce(sql,
    `(select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v20')<>${v19Seal.entryCount}`,
    `(select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v20')<>${entryCount}`,
    "Continuity archive v20 entry count guard");
  sql = replaceExactlyOnce(sql,
    `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v18')<>'${HISTORICAL_REGISTRY_SEALS.v18.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v18')<>${HISTORICAL_REGISTRY_SEALS.v18.entryCount} then raise exception 'sealed SCAC mutation registry v18 changed during successor creation'; end if;`,
    `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v19')<>'${v19Seal.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v19')<>${v19Seal.entryCount} then raise exception 'sealed SCAC mutation registry v19 changed during successor creation'; end if;`,
    "Continuity archive predecessor seal guard");
  sql = replaceExactlyOnce(sql,
    "ops.scac_policy_epoch_snapshot(),ops.scac_policy_epoch_snapshot_v6(),ops.scac_policy_epoch_snapshot_v7(),ops.scac_policy_epoch_snapshot_v8(),ops.scac_policy_epoch_snapshot_v9(),ops.scac_policy_epoch_snapshot_v10(),ops.scac_policy_epoch_snapshot_v11(),ops.scac_policy_epoch_snapshot_v12(),ops.scac_policy_epoch_snapshot_v13(),ops.scac_policy_epoch_snapshot_v14(),ops.scac_policy_epoch_snapshot_v15(),ops.scac_policy_epoch_snapshot_v16(),ops.scac_policy_epoch_snapshot_v17(),ops.scac_policy_epoch_snapshot_v18(),",
    "ops.scac_policy_epoch_snapshot(),ops.scac_policy_epoch_snapshot_v6(),ops.scac_policy_epoch_snapshot_v7(),ops.scac_policy_epoch_snapshot_v8(),ops.scac_policy_epoch_snapshot_v9(),ops.scac_policy_epoch_snapshot_v10(),ops.scac_policy_epoch_snapshot_v11(),ops.scac_policy_epoch_snapshot_v12(),ops.scac_policy_epoch_snapshot_v13(),ops.scac_policy_epoch_snapshot_v14(),ops.scac_policy_epoch_snapshot_v15(),ops.scac_policy_epoch_snapshot_v16(),ops.scac_policy_epoch_snapshot_v17(),ops.scac_policy_epoch_snapshot_v18(),ops.scac_policy_epoch_snapshot_v19(),",
    "Continuity archive historical policy snapshot revoke list");

  const seedStartMarker = "with seed as (select value as contract from jsonb_array_elements(";
  const seedEndMarker = "::jsonb))\ninsert into ops.scac_mutation_registry_entry";
  const seedStart = sql.indexOf(seedStartMarker);
  const secondSeedStart = sql.indexOf(seedStartMarker, seedStart + seedStartMarker.length);
  const seedEnd = sql.indexOf(seedEndMarker, seedStart + seedStartMarker.length);
  const secondSeedEnd = sql.indexOf(seedEndMarker, seedEnd + seedEndMarker.length);
  if (seedStart < 0 || secondSeedStart >= 0 || seedEnd < 0 || secondSeedEnd >= 0)
    throw new Error("sealed SCAC v19 migration has no exact source-seed boundary");
  const seed = JSON.stringify(rows.map(row => ({ ...row, entry_digest: `sha256:${sha256(row)}` })));
  sql = `${sql.slice(0, seedStart + seedStartMarker.length)}${sqlLiteral(seed)}${sql.slice(seedEnd)}`;

  const preflightCurrent = renderV20Current(predecessorDbCatalogBaseline);
  const preflightBegin = preflightCurrent.indexOf("begin\n");
  const preflightEnd = preflightCurrent.lastIndexOf("end $fn$;");
  if (preflightBegin < 0 || preflightEnd <= preflightBegin)
    throw new Error("generated v20 catalog predicate has no exact preflight body boundary");
  let preflightBody = preflightCurrent.slice(preflightBegin + "begin\n".length, preflightEnd);
  preflightBody = preflightBody.replaceAll(
    "then return false; end if;",
    "then raise exception 'Continuity archive pre-v20 catalog receipt drifted'; end if;");
  preflightBody = replaceExactlyOnce(preflightBody,
    `return observed_count=${predecessorDbCatalogBaseline.role_authority.count} and observed_digest='${predecessorDbCatalogBaseline.role_authority.digest}';`,
    `if observed_count<>${predecessorDbCatalogBaseline.role_authority.count} or observed_digest<>'${predecessorDbCatalogBaseline.role_authority.digest}' then raise exception 'Continuity archive pre-v20 role-authority receipt drifted'; end if;`,
    "Continuity archive pre-v20 role receipt");
  const predecessorHash = sha256(v19Migration);
  const predecessorPreflight =
`-- Exact disposable-Postgres post-0493 receipt. Refuse before any v20 function
-- exists; this registry-only successor changes no domain DDL or business rows.
do $continuity_archive_preflight$
declare observed_count integer; observed_digest text; grant_snapshot jsonb;
begin
  if (select count(*) from public.schema_migrations where filename='0493_incident_work_request_link_scac_successor.sql')<>1
     or not exists(select 1 from public.schema_migrations where filename='0493_incident_work_request_link_scac_successor.sql'
       and sha256='${predecessorHash}') then
    raise exception 'Continuity archive pre-v20 migration ledger receipt drifted';
  end if;
  grant_snapshot:=ops.scac_runtime_dml_grant_snapshot();
  if (grant_snapshot->>'entry_count')::integer<>${predecessorDbCatalogBaseline.runtime_dml_grants.count}
     or grant_snapshot->>'grant_digest'<>'${predecessorDbCatalogBaseline.runtime_dml_grants.digest}' then
    raise exception 'Continuity archive pre-v20 runtime grant receipt drifted';
  end if;
${preflightBody}end $continuity_archive_preflight$;

`;
  return `${predecessorPreflight}${sql}`.replace(/\n+$/, "\n");
}


// Shared v21 trust root. Every entry path that renders or writes a v21
// artifact calls this BEFORE doing work, so an unbound template constant can
// never reach a digest, a projection or a written file. It reads only fixed
// module constants: there is deliberately no parameter through which a caller
// could supply its own expected seal, pin or baseline.
const R06_HOOKS_CORRECTNESS_V20_MIGRATION_PATH =
  "migrations/0494_codex_continuity_archive_registry.sql";
const R06_HOOKS_CORRECTNESS_V20_RUNTIME_PATH =
  "mcp-server/src/scac-mutation-registry.v20.generated.js";

function assertR06HooksCorrectnessCatalogBaseline(label, baseline, projectionVersion) {
  if (!baseline || baseline.projection_version !== projectionVersion)
    throw new Error(
      `R06 hooks correctness ${label} catalog baseline is not ${projectionVersion}`);
  for (const category of [
    "secdef_execute", "relation_dml", "column_dml", "role_authority", "runtime_dml_grants",
  ]) {
    const receipt = baseline[category];
    if (!Number.isInteger(receipt?.count) || receipt.count < 0 ||
        !CONTINUITY_ARCHIVE_DIGEST_RE.test(receipt?.digest ?? ""))
      throw new Error(`R06 hooks correctness ${label} ${category} receipt is unbound`);
  }
}

export function assertR06HooksCorrectnessV21TrustRoot() {
  // Strictly stronger than the root it succeeds: the entire v20 chain has to
  // be bound before a v21 artifact can exist at all, so an unbound v19 limb
  // still refuses here.
  assertContinuityArchiveV20TrustRoot();
  const { v20: v20Seal } = HISTORICAL_REGISTRY_SEALS;
  if (v20Seal?.version !== REGISTRY_V20_VERSION ||
      !CONTINUITY_ARCHIVE_DIGEST_RE.test(v20Seal?.digest ?? "") ||
      !Number.isInteger(v20Seal?.entryCount) || v20Seal.entryCount < 1 ||
      !Number.isInteger(v20Seal?.sourceEntryCount) || v20Seal.sourceEntryCount < 1)
    throw new Error("R06 hooks correctness v21 predecessor seal is unbound");
  // The chain is exact in both directions: v20 is the only accepted predecessor
  // projection and v21 the only accepted successor, so the predecessor catalog
  // can never be handed back as the successor.
  assertR06HooksCorrectnessCatalogBaseline("predecessor v20",
    R06_HOOKS_CORRECTNESS_PRE_V21_DB_CATALOG_BASELINE, "scac-db-catalog-projection.v20");
  assertR06HooksCorrectnessCatalogBaseline("successor v21",
    R06_HOOKS_CORRECTNESS_FORWARD_DB_CATALOG_BASELINE, "scac-db-catalog-projection.v21");
  for (const path of [
    R06_HOOKS_CORRECTNESS_V20_MIGRATION_PATH, R06_HOOKS_CORRECTNESS_V20_RUNTIME_PATH,
  ]) {
    if (!CONTINUITY_ARCHIVE_ARTIFACT_SHA_RE.test(
      HISTORICAL_REGISTRY_ARTIFACT_SHA256[path] ?? ""))
      throw new Error(`R06 hooks correctness v21 predecessor artifact pin is unbound: ${path}`);
  }
}

// Registry-only successor for the R06 hooks-correctness contract. The source
// change re-digests hook and ops entrypoints that were already registered and
// adds no ingress, so this migration carries no domain DDL and no business
// rows; it only seals the new source inventory and installs the v21
// catalog/policy projection after the immutable v20 frontier.
export function renderR06HooksCorrectnessForwardRegistrySql(rows = fullInventory(),
  dbCatalogBaseline = R06_HOOKS_CORRECTNESS_FORWARD_DB_CATALOG_BASELINE,
  predecessorArtifacts = undefined) {
  // The predecessor seal, its catalog projection and the sealed v20 artifact
  // hashes are fixed production constants, asserted by the shared v21 trust
  // root that every v21 entry path calls. There is deliberately no caller
  // binding for them: a supplied predecessor artifact is checked AGAINST these
  // pins, it never supplies its own expected hash.
  assertR06HooksCorrectnessV21TrustRoot();
  const { v20: v20Seal } = HISTORICAL_REGISTRY_SEALS;
  const predecessorDbCatalogBaseline = R06_HOOKS_CORRECTNESS_PRE_V21_DB_CATALOG_BASELINE;
  const artifactShaRe = CONTINUITY_ARCHIVE_ARTIFACT_SHA_RE;
  // A caller-supplied successor baseline is held to the SAME exact-projection
  // shape as the fixed constant; it can only ever narrow, never widen.
  assertR06HooksCorrectnessCatalogBaseline("successor v21", dbCatalogBaseline,
    "scac-db-catalog-projection.v21");

  const v21Digest = registryDigestFor(REGISTRY_V21_VERSION, rows, dbCatalogBaseline);
  const catalogCount = dbCatalogBaseline.secdef_execute.count +
    dbCatalogBaseline.relation_dml.count + dbCatalogBaseline.column_dml.count;
  const entryCount = rows.length + catalogCount;
  const v20MigrationPath = R06_HOOKS_CORRECTNESS_V20_MIGRATION_PATH;
  const v20RuntimePath = R06_HOOKS_CORRECTNESS_V20_RUNTIME_PATH;
  const v20Rows = frozenInventory(REGISTRY_V20_VERSION);
  // A partial predecessor bundle regenerates only the missing half, and it
  // regenerates it from the canonical prior inputs: the v20 renderer's third
  // argument is its own v19-shaped predecessor bundle, so this v20-shaped one
  // is never forwarded into it.
  const v20Migration = predecessorArtifacts?.migration ??
    renderContinuityArchiveForwardRegistrySql(v20Rows, predecessorDbCatalogBaseline);
  const v20Runtime = predecessorArtifacts?.runtime ?? renderRuntimeProjection(v20Rows, {
    version: REGISTRY_V20_VERSION,
    dbCatalogBaseline: predecessorDbCatalogBaseline,
  });
  for (const [path, source] of [
    [v20MigrationPath, v20Migration], [v20RuntimePath, v20Runtime],
  ]) {
    const expected = HISTORICAL_REGISTRY_ARTIFACT_SHA256[path];
    if (!artifactShaRe.test(expected ?? ""))
      throw new Error(`R06 hooks correctness v21 predecessor artifact pin is unbound: ${path}`);
    const observed = sha256(source);
    if (observed !== expected)
      throw new Error(`sealed historical SCAC v20 artifact changed: ${path}: ${observed}`);
  }

  const headerMarker =
    "-- SCAC-12: registry-only mutation registry v20 after Codex continuity archive recovery.";
  const coreStart = v20Migration.indexOf(headerMarker);
  if (coreStart < 0 || v20Migration.indexOf(headerMarker, coreStart + headerMarker.length) >= 0)
    throw new Error("sealed SCAC v20 migration has no exact successor core boundary");
  const v20Core = v20Migration.slice(coreStart);
  const currentV20Marker = "create or replace function ops.scac_mutation_catalog_v20_current()";
  const policyMarker =
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v19;";
  const currentV20Start = v20Core.indexOf(currentV20Marker);
  const secondCurrentV20 = v20Core.indexOf(
    currentV20Marker, currentV20Start + currentV20Marker.length);
  const v19HistoryMarker =
    "alter function ops.scac_mutation_catalog_v19_current() rename to scac_mutation_catalog_v19_live_at_seal;";
  const v19HistoryStart = v20Core.indexOf(v19HistoryMarker);
  const secondV19History = v20Core.indexOf(
    v19HistoryMarker, v19HistoryStart + v19HistoryMarker.length);
  const policyStart = v20Core.indexOf(policyMarker);
  const secondPolicy = v20Core.indexOf(policyMarker, policyStart + policyMarker.length);
  if (v19HistoryStart < 0 || secondV19History >= 0 || currentV20Start <= v19HistoryStart ||
      secondCurrentV20 >= 0 || policyStart <= currentV20Start || secondPolicy >= 0)
    throw new Error("sealed SCAC v20 migration has no exact catalog successor boundary");
  const installedV19History = v20Core.slice(v19HistoryStart, currentV20Start);
  const v20Current = v20Core.slice(currentV20Start, policyStart);
  const v20History =
`alter function ops.scac_mutation_catalog_v20_current() rename to scac_mutation_catalog_v20_live_at_seal;
create or replace function ops.scac_mutation_registry_v20_seal_available()
returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$
  select ops.scac_mutation_registry_seal_valid('scac-mutation-registry.v20')
$fn$;
create or replace function ops.scac_mutation_catalog_v20_current()
returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$
  select ops.scac_mutation_catalog_v20_live_at_seal()
$fn$;
comment on function ops.scac_mutation_registry_v20_seal_available() is 'Exact immutable v20 registry seal; separate from whether the live catalog still equals v20.';
comment on function ops.scac_mutation_catalog_v20_current() is 'Historical v20 live-catalog validator; expected to become false after the v21 authority surface is installed.';

`;
  const renderV21Current = baseline => {
    let current = v20Current
      .replaceAll("scac_mutation_catalog_v20_current", "scac_mutation_catalog_v21_current")
      .replaceAll("scac-mutation-registry.v20", "scac-mutation-registry.v21");
    for (const [category, label] of [
      ["secdef_execute", "security-definer"],
      ["relation_dml", "relation"],
      ["column_dml", "column"],
    ]) {
      current = replaceExactlyOnce(current,
        `if observed_count<>${predecessorDbCatalogBaseline[category].count} or observed_digest<>'${predecessorDbCatalogBaseline[category].digest}' then return false; end if;`,
        `if observed_count<>${baseline[category].count} or observed_digest<>'${baseline[category].digest}' then return false; end if;`,
        `R06 hooks correctness v21 ${label} baseline`);
    }
    return replaceExactlyOnce(current,
      `return observed_count=${predecessorDbCatalogBaseline.role_authority.count} and observed_digest='${predecessorDbCatalogBaseline.role_authority.digest}';`,
      `return observed_count=${baseline.role_authority.count} and observed_digest='${baseline.role_authority.digest}';`,
      "R06 hooks correctness v21 role-authority baseline");
  };
  const v21Current = renderV21Current(dbCatalogBaseline);

  let sql = replaceExactlyOnce(v20Core, v20Current,
    "__CONTINUITY_ARCHIVE_V20_CATALOG_SUCCESSOR__",
    "R06 hooks correctness v20 current catalog block");
  sql = replaceExactlyOnce(sql, installedV19History, "",
    "R06 hooks correctness already-installed v19 catalog history");
  sql = replaceExactlyOnce(sql, headerMarker,
    "-- SCAC-12: registry-only mutation registry v21 after R06 hook-correctness evidence routing.",
    "R06 hooks correctness migration header");
  sql = sql
    .replaceAll("scac-mutation-registry.v20", "scac-mutation-registry.v21")
    .replaceAll("_v20", "_v21")
    .replaceAll(" v20", " v21");
  sql = replaceExactlyOnce(sql, JSON.stringify(predecessorDbCatalogBaseline),
    JSON.stringify(dbCatalogBaseline), "R06 hooks correctness v21 catalog projection");
  sql = replaceExactlyOnce(sql,
    `'${v20Seal.digest}',${v20Seal.entryCount},${v20Seal.sourceEntryCount},`,
    `'sha256:${v21Digest}',${entryCount},${rows.length},`,
    "R06 hooks correctness v21 registry row");
  sql = replaceExactlyOnce(sql,
    `ops.scac_mutation_registration_v21('${v20Seal.digest}',`,
    `ops.scac_mutation_registration_v21('sha256:${v21Digest}',`,
    "R06 hooks correctness v21 snapshot registry lookup");
  sql = replaceExactlyOnce(sql,
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v19;",
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v20;",
    "R06 hooks correctness policy snapshot predecessor");
  sql = replaceExactlyOnce(sql, "__CONTINUITY_ARCHIVE_V20_CATALOG_SUCCESSOR__",
    `${v20History}${v21Current}`, "R06 hooks correctness v20 catalog history insertion");

  const versionsThrough20 = Array.from({ length: 20 }, (_, index) =>
    `'scac-mutation-registry.v${index + 1}'`).join(",");
  const versionsThrough19 = Array.from({ length: 19 }, (_, index) =>
    `'scac-mutation-registry.v${index + 1}'`).join(",");
  sql = replaceExactlyOnce(sql,
    `check (registry_version in (${versionsThrough19},'scac-mutation-registry.v21'))`,
    `check (registry_version in (${versionsThrough20},'scac-mutation-registry.v21'))`,
    "R06 hooks correctness registry-version constraint");
  sql = replaceExactlyOnce(sql,
    `if p_registry_version not in (${versionsThrough19}) then return false; end if;`,
    `if p_registry_version not in (${versionsThrough20}) then return false; end if;`,
    "R06 hooks correctness historical seal allowlist");
  sql = replaceExactlyOnce(sql,
    `    when 'scac-mutation-registry.v19' then '${HISTORICAL_REGISTRY_SEALS.v19.digest}' end;`,
    `    when 'scac-mutation-registry.v19' then '${HISTORICAL_REGISTRY_SEALS.v19.digest}'\n    when '${v20Seal.version}' then '${v20Seal.digest}' end;`,
    "R06 hooks correctness historical digest case");
  sql = replaceExactlyOnce(sql,
    `    when 'scac-mutation-registry.v19' then '${JSON.stringify(INCIDENT_WORK_REQUEST_LINK_FORWARD_DB_CATALOG_BASELINE)}'::jsonb end;`,
    `    when 'scac-mutation-registry.v19' then '${JSON.stringify(INCIDENT_WORK_REQUEST_LINK_FORWARD_DB_CATALOG_BASELINE)}'::jsonb\n    when '${v20Seal.version}' then '${JSON.stringify(predecessorDbCatalogBaseline)}'::jsonb end;`,
    "R06 hooks correctness historical catalog case");
  sql = replaceExactlyOnce(sql,
    `    ('scac-mutation-registry.v19','${HISTORICAL_REGISTRY_SEALS.v19.digest}',${HISTORICAL_REGISTRY_SEALS.v19.entryCount},${HISTORICAL_REGISTRY_SEALS.v19.sourceEntryCount})\n`,
    `    ('scac-mutation-registry.v19','${HISTORICAL_REGISTRY_SEALS.v19.digest}',${HISTORICAL_REGISTRY_SEALS.v19.entryCount},${HISTORICAL_REGISTRY_SEALS.v19.sourceEntryCount}),\n    ('${v20Seal.version}','${v20Seal.digest}',${v20Seal.entryCount},${v20Seal.sourceEntryCount})\n`,
    "R06 hooks correctness historical seal tuple");
  sql = replaceExactlyOnce(sql,
    "    ops.scac_mutation_registry_v19_seal_available()) then",
    "    ops.scac_mutation_registry_v19_seal_available() and\n    ops.scac_mutation_registry_v20_seal_available()) then",
    "R06 hooks correctness snapshot predecessor seal");
  sql = replaceExactlyOnce(sql,
    `or (r.registry_version='scac-mutation-registry.v21' and r.registry_digest='${v20Seal.digest}')`,
    `or (r.registry_version='scac-mutation-registry.v20' and r.registry_digest='${v20Seal.digest}')\n         or (r.registry_version='scac-mutation-registry.v21' and r.registry_digest='sha256:${v21Digest}')`,
    "R06 hooks correctness epoch-chain digest cases");
  sql = replaceExactlyOnce(sql,
    `  (registry_version='scac-mutation-registry.v21' and registry_digest='${v20Seal.digest}')`,
    `  (registry_version='scac-mutation-registry.v20' and registry_digest='${v20Seal.digest}') or\n  (registry_version='scac-mutation-registry.v21' and registry_digest='sha256:${v21Digest}')`,
    "R06 hooks correctness epoch constraint digest cases");
  sql = replaceExactlyOnce(sql,
    `'{registry_digest}',to_jsonb('${v20Seal.digest}'::text)`,
    `'{registry_digest}',to_jsonb('sha256:${v21Digest}'::text)`,
    "R06 hooks correctness snapshot registry digest");
  sql = replaceExactlyOnce(sql,
    "ops.scac_mutation_registry_v18_seal_available(),ops.scac_mutation_catalog_v19_live_at_seal(),ops.scac_mutation_catalog_v19_current(),ops.scac_mutation_registry_v19_seal_available(),ops.scac_mutation_catalog_v21_current()",
    "ops.scac_mutation_registry_v18_seal_available(),ops.scac_mutation_catalog_v19_live_at_seal(),ops.scac_mutation_catalog_v19_current(),ops.scac_mutation_registry_v19_seal_available(),ops.scac_mutation_catalog_v20_live_at_seal(),ops.scac_mutation_catalog_v20_current(),ops.scac_mutation_registry_v20_seal_available(),ops.scac_mutation_catalog_v21_current()",
    "R06 hooks correctness historical function revoke list");
  sql = replaceExactlyOnce(sql,
    "Continuity archive successor snapshot: current policy epochs bind mutation registry v21 while historical v2/v3/v4/v5/v6/v7/v8/v9/v10/v11/v12/v13/v14/v15/v16/v17/v18/v19 epochs remain immutable.",
    "R06 hooks-correctness successor snapshot: current policy epochs bind mutation registry v21 while historical v2/v3/v4/v5/v6/v7/v8/v9/v10/v11/v12/v13/v14/v15/v16/v17/v18/v19/v20 epochs remain immutable.",
    "R06 hooks correctness policy snapshot comment");
  sql = replaceExactlyOnce(sql,
    `(select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v21')<>${v20Seal.entryCount}`,
    `(select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v21')<>${entryCount}`,
    "R06 hooks correctness v21 entry count guard");
  sql = replaceExactlyOnce(sql,
    `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v19')<>'${HISTORICAL_REGISTRY_SEALS.v19.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v19')<>${HISTORICAL_REGISTRY_SEALS.v19.entryCount} then raise exception 'sealed SCAC mutation registry v19 changed during successor creation'; end if;`,
    `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v20')<>'${v20Seal.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v20')<>${v20Seal.entryCount} then raise exception 'sealed SCAC mutation registry v20 changed during successor creation'; end if;`,
    "R06 hooks correctness predecessor seal guard");
  sql = replaceExactlyOnce(sql,
    "ops.scac_policy_epoch_snapshot(),ops.scac_policy_epoch_snapshot_v6(),ops.scac_policy_epoch_snapshot_v7(),ops.scac_policy_epoch_snapshot_v8(),ops.scac_policy_epoch_snapshot_v9(),ops.scac_policy_epoch_snapshot_v10(),ops.scac_policy_epoch_snapshot_v11(),ops.scac_policy_epoch_snapshot_v12(),ops.scac_policy_epoch_snapshot_v13(),ops.scac_policy_epoch_snapshot_v14(),ops.scac_policy_epoch_snapshot_v15(),ops.scac_policy_epoch_snapshot_v16(),ops.scac_policy_epoch_snapshot_v17(),ops.scac_policy_epoch_snapshot_v18(),ops.scac_policy_epoch_snapshot_v19(),",
    "ops.scac_policy_epoch_snapshot(),ops.scac_policy_epoch_snapshot_v6(),ops.scac_policy_epoch_snapshot_v7(),ops.scac_policy_epoch_snapshot_v8(),ops.scac_policy_epoch_snapshot_v9(),ops.scac_policy_epoch_snapshot_v10(),ops.scac_policy_epoch_snapshot_v11(),ops.scac_policy_epoch_snapshot_v12(),ops.scac_policy_epoch_snapshot_v13(),ops.scac_policy_epoch_snapshot_v14(),ops.scac_policy_epoch_snapshot_v15(),ops.scac_policy_epoch_snapshot_v16(),ops.scac_policy_epoch_snapshot_v17(),ops.scac_policy_epoch_snapshot_v18(),ops.scac_policy_epoch_snapshot_v19(),ops.scac_policy_epoch_snapshot_v20(),",
    "R06 hooks correctness historical policy snapshot revoke list");

  const seedStartMarker = "with seed as (select value as contract from jsonb_array_elements(";
  const seedEndMarker = "::jsonb))\ninsert into ops.scac_mutation_registry_entry";
  const seedStart = sql.indexOf(seedStartMarker);
  const secondSeedStart = sql.indexOf(seedStartMarker, seedStart + seedStartMarker.length);
  const seedEnd = sql.indexOf(seedEndMarker, seedStart + seedStartMarker.length);
  const secondSeedEnd = sql.indexOf(seedEndMarker, seedEnd + seedEndMarker.length);
  if (seedStart < 0 || secondSeedStart >= 0 || seedEnd < 0 || secondSeedEnd >= 0)
    throw new Error("sealed SCAC v20 migration has no exact source-seed boundary");
  const seed = JSON.stringify(rows.map(row => ({ ...row, entry_digest: `sha256:${sha256(row)}` })));
  sql = `${sql.slice(0, seedStart + seedStartMarker.length)}${sqlLiteral(seed)}${sql.slice(seedEnd)}`;

  const preflightCurrent = renderV21Current(predecessorDbCatalogBaseline);
  const preflightBegin = preflightCurrent.indexOf("begin\n");
  const preflightEnd = preflightCurrent.lastIndexOf("end $fn$;");
  if (preflightBegin < 0 || preflightEnd <= preflightBegin)
    throw new Error("generated v21 catalog predicate has no exact preflight body boundary");
  let preflightBody = preflightCurrent.slice(preflightBegin + "begin\n".length, preflightEnd);
  preflightBody = preflightBody.replaceAll(
    "then return false; end if;",
    "then raise exception 'R06 hooks correctness pre-v21 catalog receipt drifted'; end if;");
  preflightBody = replaceExactlyOnce(preflightBody,
    `return observed_count=${predecessorDbCatalogBaseline.role_authority.count} and observed_digest='${predecessorDbCatalogBaseline.role_authority.digest}';`,
    `if observed_count<>${predecessorDbCatalogBaseline.role_authority.count} or observed_digest<>'${predecessorDbCatalogBaseline.role_authority.digest}' then raise exception 'R06 hooks correctness pre-v21 role-authority receipt drifted'; end if;`,
    "R06 hooks correctness pre-v21 role receipt");
  const predecessorHash = sha256(v20Migration);
  const predecessorPreflight =
`-- Exact disposable-Postgres post-0494 receipt. Refuse before any v21 function
-- exists; this registry-only successor changes no domain DDL or business rows.
do $r06_hooks_correctness_preflight$
declare observed_count integer; observed_digest text; grant_snapshot jsonb;
begin
  if (select count(*) from public.schema_migrations where filename='0494_codex_continuity_archive_registry.sql')<>1
     or not exists(select 1 from public.schema_migrations where filename='0494_codex_continuity_archive_registry.sql'
       and sha256='${predecessorHash}') then
    raise exception 'R06 hooks correctness pre-v21 migration ledger receipt drifted';
  end if;
  grant_snapshot:=ops.scac_runtime_dml_grant_snapshot();
  if (grant_snapshot->>'entry_count')::integer<>${predecessorDbCatalogBaseline.runtime_dml_grants.count}
     or grant_snapshot->>'grant_digest'<>'${predecessorDbCatalogBaseline.runtime_dml_grants.digest}' then
    raise exception 'R06 hooks correctness pre-v21 runtime grant receipt drifted';
  end if;
${preflightBody}end $r06_hooks_correctness_preflight$;

`;
  return `${predecessorPreflight}${sql}`.replace(/\n+$/, "\n");
}


// Shared v22 trust root. Every entry path that renders or writes a v22
// artifact calls this BEFORE doing work, so an unbound template constant can
// never reach a digest, a projection or a written file.
const DOCTORCRE_PORTFOLIO_V21_MIGRATION_PATH =
  "migrations/0495_r06_hooks_correctness_scac_successor.sql";
const DOCTORCRE_PORTFOLIO_V21_RUNTIME_PATH =
  "mcp-server/src/scac-mutation-registry.v21.generated.js";

export function assertDoctorcrePortfolioV22TrustRoot() {
  // Strictly stronger than the root it succeeds: the whole v21 chain has to be
  // bound before a v22 artifact can exist at all.
  assertR06HooksCorrectnessV21TrustRoot();
  const { v21: v21Seal } = HISTORICAL_REGISTRY_SEALS;
  if (v21Seal?.version !== REGISTRY_V21_VERSION ||
      !CONTINUITY_ARCHIVE_DIGEST_RE.test(v21Seal?.digest ?? "") ||
      !Number.isInteger(v21Seal?.entryCount) || v21Seal.entryCount < 1 ||
      !Number.isInteger(v21Seal?.sourceEntryCount) || v21Seal.sourceEntryCount < 1)
    throw new Error("DoctorCRE portfolio v22 predecessor seal is unbound");
  assertR06HooksCorrectnessCatalogBaseline("predecessor v21",
    DOCTORCRE_PORTFOLIO_PRE_V22_DB_CATALOG_BASELINE, "scac-db-catalog-projection.v21");
  assertR06HooksCorrectnessCatalogBaseline("successor v22",
    DOCTORCRE_PORTFOLIO_FORWARD_DB_CATALOG_BASELINE, "scac-db-catalog-projection.v22");
  for (const path of [
    DOCTORCRE_PORTFOLIO_V21_MIGRATION_PATH, DOCTORCRE_PORTFOLIO_V21_RUNTIME_PATH,
  ]) {
    if (!CONTINUITY_ARCHIVE_ARTIFACT_SHA_RE.test(
      HISTORICAL_REGISTRY_ARTIFACT_SHA256[path] ?? ""))
      throw new Error(`DoctorCRE portfolio v22 predecessor artifact pin is unbound: ${path}`);
  }
}

// The append-only portfolio hierarchy itself. It is a template literal rather
// than a separate file because the migration is generated: the domain SQL and
// the catalog seal it produces have to be emitted together or the sealed
// catalog would describe a database the migration never built.
const DOCTORCRE_PORTFOLIO_DOMAIN_SQL = String.raw`
-- DoctorCRE v5 portfolio hierarchy: append-only typed persistence.
--
-- Proposal is inert, review is independent and exact-digest, acceptance is a
-- verified partner act on an exact hash. None of the three creates a job, an
-- execution envelope, a capability session, a schedule, a deployment or a
-- clock; the existing Engineering admission path gains an ancestor and
-- predecessor check and gains no second queue.
--
-- TWO DIGESTS, DELIBERATELY. The GRAPH digest answers "is this the settled
-- 21-node four-child shape". The ACCEPTED digest answers "is this the exact
-- thing a partner approved", and it covers strictly more: the graph plus every
-- child binding -- identity, ordinal, version, the child's own digest and its
-- applicable accepted source. Those three decide what a descendant inherits, so
-- a hash that omitted them would let the governing facts move under an
-- unchanged signature. Acceptance binds the ACCEPTED digest.
--
-- NO ACTOR ARRIVES IN A PAYLOAD. Proposal and review derive the writer from the
-- transaction-local actor context the server sets from verified session state;
-- acceptance derives the partner from ops.authority_actor_slug(), which reads
-- session_user. Direct INSERT is granted to nobody, so the derivation cannot be
-- stepped around with raw SQL.


-- ---------------------------------------------------------------------------
-- Shared canonicalization helpers.
-- ---------------------------------------------------------------------------

-- A budget ceiling is a JSON number on both sides of the digest, so PostgreSQL
-- has to spell it the way JavaScript does. JavaScript switches to exponent form
-- below 1e-6 and at or above 1e21; PostgreSQL's float8 text switches below
-- roughly 1e-4, and numeric text never does. Rendering from the double's own
-- shortest representation and then re-spelling it to JavaScript's thresholds is
-- what makes 1e-7 a value we CARRY rather than a value we refuse.
create or replace function ops.portfolio_canonical_number_text(p_value numeric)
returns text language plpgsql immutable
set search_path = pg_catalog set extra_float_digits = 1
as $$
declare v_shortest text; v_mantissa text; v_exponent integer; v_plain text;
begin
  if p_value is null then return null; end if;
  if p_value = 0 then return '0'; end if;
  v_shortest := (p_value::float8)::text;
  if strpos(v_shortest, 'e') = 0 then
    v_plain := p_value::text;
  else
    v_mantissa := split_part(v_shortest, 'e', 1);
    v_exponent := split_part(v_shortest, 'e', 2)::integer;
    if v_exponent < -6 or v_exponent >= 21 then
      -- JavaScript's exponent form, without PostgreSQL's zero padding.
      return v_mantissa || 'e' || v_exponent::text;
    end if;
    v_plain := p_value::text;
  end if;
  if strpos(v_plain, '.') > 0 then
    v_plain := rtrim(rtrim(v_plain, '0'), '.');
  end if;
  return coalesce(nullif(v_plain, ''), '0');
end;
$$;

-- Representable means the value IS a double's value. Extra precision a double
-- cannot hold is refused at the boundary; a value JavaScript can express is not.
create or replace function ops.portfolio_canonical_number_valid(p_value numeric)
returns boolean language sql immutable
set search_path = pg_catalog set extra_float_digits = 1
as $$
  select p_value is not null and p_value = ((p_value::float8)::text)::numeric
$$;

comment on function ops.portfolio_canonical_number_text(numeric) is
  'One numeric rendered exactly as JavaScript renders it, including JavaScript exponent thresholds.';
comment on function ops.portfolio_canonical_number_valid(numeric) is
  'True when a numeric is a value a double can hold. Extra precision refuses; every JavaScript-expressible value is carried.';

-- Portfolio-local canonical JSON. It matches the module canonicalJson exactly:
-- object keys sorted by code unit, array order preserved, strings via to_jsonb,
-- and NUMBERS through the JavaScript-faithful renderer above. The shared
-- guidance canonicalizer renders numbers in jsonb's own spelling, which is
-- correct for the integral values it was written for and wrong here, so this is
-- a separate function rather than a change to a historical shared one.
create or replace function ops.portfolio_canonical_json(p_value jsonb)
returns text language plpgsql immutable
set search_path = pg_catalog, ops
as $$
declare v_kind text := jsonb_typeof(p_value); v_result text;
begin
  if v_kind = 'object' then
    select '{' || coalesce(string_agg(
             to_jsonb(entry.key)::text || ':' || ops.portfolio_canonical_json(entry.value),
             ',' order by entry.key collate "C"), '') || '}'
      into v_result from jsonb_each(p_value) as entry(key, value);
    return v_result;
  elsif v_kind = 'array' then
    select '[' || coalesce(string_agg(
             ops.portfolio_canonical_json(entry.value), ',' order by entry.ordinality), '') || ']'
      into v_result from jsonb_array_elements(p_value) with ordinality as entry(value, ordinality);
    return v_result;
  elsif v_kind = 'string' then
    return to_jsonb(p_value #>> '{}')::text;
  elsif v_kind = 'number' then
    return ops.portfolio_canonical_number_text((p_value #>> '{}')::numeric);
  end if;
  return p_value::text;
end;
$$;

comment on function ops.portfolio_canonical_json(jsonb) is
  'Canonical JSON for portfolio digests, matching the module canonicalJson including JavaScript number rendering.';

-- A check constraint cannot hold a subquery, so the closed shape of the model
-- floor is an immutable predicate the constraint calls.
create or replace function ops.portfolio_model_floor_valid(p_floor jsonb)
returns boolean language sql immutable
set search_path = pg_catalog
as $$
  select jsonb_typeof(p_floor) = 'object'
     and (select coalesce(array_agg(k order by k collate "C"), '{}')
            from jsonb_object_keys(p_floor) k) = array['effort','model','provider','version']
     and (select bool_and(jsonb_typeof(p_floor -> k) = 'string'
                          and btrim(p_floor ->> k) <> '')
            from jsonb_object_keys(p_floor) k)
$$;

comment on function ops.portfolio_model_floor_valid(jsonb) is
  'True when a node model floor is exactly {effort, model, provider, version} with non-empty string values.';

-- The writer whose authorship the server established for this transaction.
-- This is a DERIVATION, not an authentication: what authenticates the write is
-- that only the server sets this context and that no role holds direct INSERT.
create or replace function ops.portfolio_writer_actor_id()
returns uuid language plpgsql stable
set search_path = pg_catalog, ops, public
as $$
declare v_slug text; v_id uuid; v_human boolean; v_verified text;
begin
  v_slug := nullif(current_setting('carr.acting_actor_slug', true), '');
  if v_slug is null then
    raise exception 'portfolio writes require the server-established actor context; none is set on this transaction';
  end if;
  select id, kind = 'human' into v_id, v_human from public.actor where slug = v_slug and active;
  if not found then
    raise exception 'acting actor % is not an active actor', v_slug;
  end if;
  -- Writing AS A HUMAN needs the second, narrower context the server sets only
  -- for a verified partner. Without it a sponsored agent could name a partner
  -- as the author of its own work.
  if v_human then
    v_verified := nullif(current_setting('carr.verified_human_actor_slug', true), '');
    if v_verified is distinct from v_slug then
      raise exception 'writing as human actor % requires the verified-partner context, which names %',
        v_slug, coalesce(v_verified, '(none)');
    end if;
  end if;
  return v_id;
end;
$$;

comment on function ops.portfolio_writer_actor_id() is
  'The active actor the server established for this transaction through carr.acting_actor_slug. Portfolio proposal and review derive authorship from it instead of accepting an actor in the payload.';

-- ---------------------------------------------------------------------------
-- Revision: one immutable graph plus the accepted binding over it.
-- ---------------------------------------------------------------------------
create table if not exists ops.portfolio_revision (
  id                    uuid primary key default gen_random_uuid(),
  portfolio_ref         text not null check (portfolio_ref ~ '^[A-Za-z0-9][A-Za-z0-9:._-]{2,199}$'),
  revision_version      integer not null check (revision_version > 0),
  idempotency_key       uuid not null unique,
  schema_version        text not null check (schema_version = 'doctorcre-v5-portfolio-revision.v1'),
  accepted_schema_version text not null
                          check (accepted_schema_version = 'doctorcre-v5-portfolio-accepted-revision.v1'),
  preimage              jsonb not null check (jsonb_typeof(preimage) = 'object'),
  graph_digest          text not null check (graph_digest ~ '^sha256:[0-9a-f]{64}$'),
  accepted_digest       text not null check (accepted_digest ~ '^sha256:[0-9a-f]{64}$'),
  node_count            integer not null check (node_count = 21),
  edge_count            integer not null check (edge_count >= 0),
  child_count           integer not null check (child_count = 4),
  proposed_by_actor_id  uuid not null references public.actor(id),
  created_at            timestamptz not null default now(),
  unique (portfolio_ref, revision_version),
  unique (portfolio_ref, accepted_digest)
);

comment on table ops.portfolio_revision is
  'Append-only DoctorCRE v5 portfolio revision. graph_digest covers the settled 21-node shape; accepted_digest additionally covers every child binding and is the hash a partner accepts. Inert: creates no job, envelope, capability or schedule.';

-- ---------------------------------------------------------------------------
-- Children: four immutable identities, each with its own version, its own hash
-- over its own content, and its applicable accepted source binding. The source
-- binding points at the EXISTING Program 6 plan acceptance; no second authority
-- ledger is created.
-- ---------------------------------------------------------------------------
create table if not exists ops.portfolio_child_revision (
  id                    uuid primary key default gen_random_uuid(),
  portfolio_revision_id uuid not null references ops.portfolio_revision(id),
  child_ref             text not null check (child_ref in
                          ('foundation-and-control-plane','assurance-fabric',
                           'product-journeys','rollout-and-retirement')),
  child_ordinal         integer not null check (child_ordinal between 0 and 3),
  child_version         integer not null check (child_version > 0),
  child_digest          text not null check (child_digest ~ '^sha256:[0-9a-f]{64}$'),
  accepted_plan_id      uuid references ops.sourced_work_request_plan(id),
  created_at            timestamptz not null default now(),
  unique (portfolio_revision_id, child_ref),
  unique (portfolio_revision_id, child_ordinal)
);

comment on table ops.portfolio_child_revision is
  'The four immutable DoctorCRE v5 child programs for one portfolio revision, each with its own version, content digest and applicable accepted source binding.';

-- ---------------------------------------------------------------------------
-- Nodes: the 21 master milestones with COMPLETE typed metadata. Every metadata
-- column is NOT NULL: a node governed by an authority class or budget it does
-- not carry is not governed at all.
-- ---------------------------------------------------------------------------
create table if not exists ops.portfolio_node (
  id                    uuid primary key default gen_random_uuid(),
  portfolio_revision_id uuid not null references ops.portfolio_revision(id),
  node_ref              text not null check (node_ref ~ '^[A-Za-z0-9][A-Za-z0-9:._-]{2,199}$'),
  node_kind             text not null check (node_kind in ('portfolio','child','milestone','slice')),
  ordinal               integer not null check (ordinal between 1 and 21),
  parent_ref            text not null check (parent_ref ~ '^[A-Za-z0-9][A-Za-z0-9:._-]{2,199}$'),
  child_ref             text not null check (child_ref in
                          ('foundation-and-control-plane','assurance-fabric',
                           'product-journeys','rollout-and-retirement')),
  authority_class       text not null check (authority_class ~ '^[a-z][a-z0-9_]{1,63}$'),
  effect_class          text not null check (effect_class ~ '^[a-z][a-z0-9_]{1,63}$'),
  data_class            text not null check (data_class ~ '^[a-z][a-z0-9_]{1,63}$'),
  budget_identity       text not null check (btrim(budget_identity) <> '' and char_length(budget_identity) <= 200),
  budget_ceiling        numeric not null check (budget_ceiling >= 0 and budget_ceiling <= 1000000000000
                          and ops.portfolio_canonical_number_valid(budget_ceiling)),
  model_floor           jsonb not null,
  recovery_ref          text not null check (recovery_ref ~ '^[A-Za-z0-9][A-Za-z0-9:._-]{2,199}$'),
  terminal_predicate    text not null check (btrim(terminal_predicate) <> '' and char_length(terminal_predicate) <= 500),
  created_at            timestamptz not null default now(),
  constraint portfolio_node_model_floor_closed check (ops.portfolio_model_floor_valid(model_floor)),
  constraint portfolio_node_not_self_parent check (parent_ref <> node_ref),
  unique (portfolio_revision_id, node_ref),
  unique (portfolio_revision_id, ordinal)
);

comment on table ops.portfolio_node is
  'The 21 master milestone nodes of one portfolio revision, with complete typed metadata and the child program that governs each.';

create table if not exists ops.portfolio_node_edge (
  id                    uuid primary key default gen_random_uuid(),
  portfolio_revision_id uuid not null references ops.portfolio_revision(id),
  from_node_ref         text not null,
  to_node_ref           text not null,
  created_at            timestamptz not null default now(),
  constraint portfolio_edge_not_self check (from_node_ref <> to_node_ref),
  unique (portfolio_revision_id, from_node_ref, to_node_ref)
);

comment on table ops.portfolio_node_edge is
  'Dependency edges of one portfolio revision; from_node_ref must complete before to_node_ref.';

create table if not exists ops.portfolio_revision_review (
  id                    uuid primary key default gen_random_uuid(),
  portfolio_revision_id uuid not null references ops.portfolio_revision(id),
  idempotency_key       uuid not null unique,
  reviewed_digest       text not null check (reviewed_digest ~ '^sha256:[0-9a-f]{64}$'),
  verdict               text not null check (verdict in ('pass','fail')),
  review_summary        text not null check (btrim(review_summary) <> '' and char_length(review_summary) <= 1000),
  reviewer_actor_id     uuid not null references public.actor(id),
  created_at            timestamptz not null default now()
);

comment on table ops.portfolio_revision_review is
  'Append-only independent review of one exact accepted digest. A review naming a digest the revision does not currently have is refused at write time, so a passing review can never be carried onto different bytes.';

create table if not exists ops.portfolio_revision_acceptance_receipt (
  id                    uuid primary key default gen_random_uuid(),
  portfolio_revision_id uuid not null unique references ops.portfolio_revision(id),
  portfolio_ref         text not null,
  idempotency_key       uuid not null unique,
  accepted_digest       text not null check (accepted_digest ~ '^sha256:[0-9a-f]{64}$'),
  review_id             uuid not null unique references ops.portfolio_revision_review(id),
  accepted_by_actor_id  uuid not null references public.actor(id),
  accepted_at           timestamptz not null default now()
);

comment on table ops.portfolio_revision_acceptance_receipt is
  'Private verified-partner receipt accepting one exact portfolio accepted digest. It grants no dispatch or execution authority; it only makes that revision the current accepted ancestor.';

-- ---------------------------------------------------------------------------
-- Append-only enforcement, and the freeze that follows acceptance.
-- ---------------------------------------------------------------------------
create or replace function ops.portfolio_rows_immutable()
returns trigger language plpgsql
set search_path = pg_catalog, ops
as $$
begin
  raise exception 'DoctorCRE v5 portfolio rows are append-only';
end;
$$;

comment on function ops.portfolio_rows_immutable() is
  'Refuses every update and delete on the DoctorCRE v5 portfolio hierarchy tables.';

do $$
declare t text;
begin
  foreach t in array array[
    'portfolio_revision','portfolio_child_revision','portfolio_node',
    'portfolio_node_edge','portfolio_revision_review',
    'portfolio_revision_acceptance_receipt'
  ] loop
    execute format('drop trigger if exists %I on ops.%I', t || '_append_only', t);
    execute format(
      'create trigger %I before update or delete on ops.%I for each row execute function ops.portfolio_rows_immutable()',
      t || '_append_only', t);
  end loop;
end $$;

-- Append-only blocks rewriting an accepted revision. It does NOT block ADDING
-- to one, and a structural row appended after acceptance would change what the
-- accepted hash covers while the receipt still read as valid. So once a
-- revision is accepted its structure is closed to inserts too.
create or replace function ops.portfolio_structure_frozen_after_acceptance()
returns trigger language plpgsql
set search_path = pg_catalog, ops
as $$
begin
  if exists (select 1 from ops.portfolio_revision_acceptance_receipt
              where portfolio_revision_id = new.portfolio_revision_id) then
    raise exception 'portfolio revision % is accepted; its structure is closed to further % rows',
      new.portfolio_revision_id, tg_table_name;
  end if;
  return new;
end;
$$;

comment on function ops.portfolio_structure_frozen_after_acceptance() is
  'Refuses a child, node or edge insert against a revision that already carries an acceptance receipt, so no structural row can appear outside the hash the partner accepted.';

do $$
declare t text;
begin
  foreach t in array array[
    'portfolio_child_revision','portfolio_node','portfolio_node_edge'
  ] loop
    execute format('drop trigger if exists %I on ops.%I', t || '_frozen_after_acceptance', t);
    execute format(
      'create trigger %I before insert on ops.%I for each row execute function ops.portfolio_structure_frozen_after_acceptance()',
      t || '_frozen_after_acceptance', t);
  end loop;
end $$;

-- ---------------------------------------------------------------------------
-- Canonical preimages, rebuilt FROM THE STORED ROWS. Recomputing from structure
-- is what makes a digest a statement about the persisted hierarchy rather than
-- about a blob a caller once supplied.
--
-- Ordering is declared: nodes and child bindings keep ordinal order; edges and
-- child member lists are unordered sets sorted by code unit (collate "C"),
-- matching the JavaScript comparator exactly.
-- ---------------------------------------------------------------------------
create or replace function ops.portfolio_graph_preimage(p_revision_id uuid)
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_rev ops.portfolio_revision%rowtype;
  v_children jsonb; v_nodes jsonb; v_edges jsonb;
begin
  select * into v_rev from ops.portfolio_revision where id = p_revision_id;
  if not found then
    raise exception 'portfolio revision % does not exist', p_revision_id;
  end if;

  select coalesce(jsonb_agg(c.child_ref order by c.child_ordinal), '[]'::jsonb)
    into v_children from ops.portfolio_child_revision c
   where c.portfolio_revision_id = p_revision_id;

  select coalesce(jsonb_agg(node_object order by ordinal), '[]'::jsonb)
    into v_nodes from (
      select n.ordinal, jsonb_build_object(
               'node_ref', n.node_ref, 'node_kind', n.node_kind, 'ordinal', n.ordinal,
               'parent_ref', n.parent_ref,
               'authority_class', n.authority_class, 'effect_class', n.effect_class,
               'data_class', n.data_class, 'budget_identity', n.budget_identity,
               'budget_ceiling', n.budget_ceiling, 'model_floor', n.model_floor,
               'recovery_ref', n.recovery_ref, 'terminal_predicate', n.terminal_predicate) as node_object
        from ops.portfolio_node n where n.portfolio_revision_id = p_revision_id) ordered;

  select coalesce(jsonb_agg(edge_object order by from_ref collate "C", to_ref collate "C"), '[]'::jsonb)
    into v_edges from (
      select e.from_node_ref as from_ref, e.to_node_ref as to_ref,
             jsonb_build_object('from_node_ref', e.from_node_ref,
                                'to_node_ref', e.to_node_ref) as edge_object
        from ops.portfolio_node_edge e where e.portfolio_revision_id = p_revision_id) ordered;

  return jsonb_build_object(
    'schema_version', v_rev.schema_version,
    'revision_version', v_rev.revision_version,
    'portfolio_ref', v_rev.portfolio_ref,
    'source_digests', v_rev.preimage -> 'source_digests',
    'child_program_refs', v_children,
    'nodes', v_nodes,
    'edges', v_edges);
end;
$$;

comment on function ops.portfolio_graph_preimage(uuid) is
  'Canonical graph preimage of one portfolio revision, rebuilt from its persisted rows.';

-- Digests use ops.portfolio_canonical_json, the portfolio-local canonicalizer.
-- It is not a gratuitous second copy: the shared guidance canonicalizer renders
-- numbers in jsonb's spelling, which cannot express 1e-7 the way JavaScript
-- does, and that function is historical shared source this change may not edit.
create or replace function ops.portfolio_graph_digest(p_revision_id uuid)
returns text language sql stable security definer
set search_path = pg_catalog, ops, public
as $$
  select 'sha256:' || encode(digest(convert_to(
    ops.portfolio_canonical_json(ops.portfolio_graph_preimage(p_revision_id)),
    'UTF8'), 'sha256'), 'hex')
$$;

comment on function ops.portfolio_graph_digest(uuid) is
  'Deterministic sha256 of one portfolio revision graph preimage.';

-- One child's own content: identity, version, ordered membership and the
-- accepted source binding that applies to it. The child hashes only what it
-- owns, so the parent can hash the child bindings without recursion.
create or replace function ops.portfolio_child_preimage(p_revision_id uuid, p_child_ref text)
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare v_child ops.portfolio_child_revision%rowtype; v_members jsonb; v_plan_ref text;
begin
  select * into v_child from ops.portfolio_child_revision
   where portfolio_revision_id = p_revision_id and child_ref = p_child_ref;
  if not found then
    raise exception 'portfolio revision % has no child %', p_revision_id, p_child_ref;
  end if;
  select coalesce(jsonb_agg(n.node_ref order by n.node_ref collate "C"), '[]'::jsonb)
    into v_members from ops.portfolio_node n
   where n.portfolio_revision_id = p_revision_id and n.child_ref = p_child_ref;
  -- The digest binds the stable plan REFERENCE, never the surrogate row id.
  select p.plan_ref into v_plan_ref from ops.sourced_work_request_plan p
   where p.id = v_child.accepted_plan_id;
  return jsonb_build_object(
    'schema_version', 'doctorcre-v5-portfolio-child.v1',
    'child_ref', v_child.child_ref,
    'child_version', v_child.child_version,
    'member_node_refs', v_members,
    'accepted_plan_ref', coalesce(to_jsonb(v_plan_ref), 'null'::jsonb));
end;
$$;

comment on function ops.portfolio_child_preimage(uuid, text) is
  'Canonical preimage of one child program: identity, version, ordered membership and applicable accepted source reference.';

create or replace function ops.portfolio_child_digest(p_revision_id uuid, p_child_ref text)
returns text language sql stable security definer
set search_path = pg_catalog, ops, public
as $$
  select 'sha256:' || encode(digest(convert_to(
    ops.portfolio_canonical_json(ops.portfolio_child_preimage(p_revision_id, p_child_ref)),
    'UTF8'), 'sha256'), 'hex')
$$;

comment on function ops.portfolio_child_digest(uuid, text) is
  'Deterministic sha256 of one child program content preimage.';

-- The accepted preimage: the graph plus every child binding. This is what a
-- partner accepts.
create or replace function ops.portfolio_accepted_preimage(p_revision_id uuid)
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare v_bindings jsonb;
begin
  select coalesce(jsonb_agg(binding order by child_ordinal), '[]'::jsonb)
    into v_bindings from (
      select c.child_ordinal, jsonb_build_object(
               'child_ref', c.child_ref,
               'child_ordinal', c.child_ordinal,
               'child_version', c.child_version,
               'child_digest', c.child_digest,
               'accepted_plan_ref', coalesce(to_jsonb(p.plan_ref), 'null'::jsonb)) as binding
        from ops.portfolio_child_revision c
        left join ops.sourced_work_request_plan p on p.id = c.accepted_plan_id
       where c.portfolio_revision_id = p_revision_id) ordered;
  return jsonb_build_object(
    'schema_version', 'doctorcre-v5-portfolio-accepted-revision.v1',
    'graph', ops.portfolio_graph_preimage(p_revision_id),
    'child_bindings', v_bindings);
end;
$$;

comment on function ops.portfolio_accepted_preimage(uuid) is
  'Canonical accepted preimage: the graph plus every child binding. Nothing that governs a descendant sits outside it.';

create or replace function ops.portfolio_accepted_digest(p_revision_id uuid)
returns text language sql stable security definer
set search_path = pg_catalog, ops, public
as $$
  select 'sha256:' || encode(digest(convert_to(
    ops.portfolio_canonical_json(ops.portfolio_accepted_preimage(p_revision_id)),
    'UTF8'), 'sha256'), 'hex')
$$;

comment on function ops.portfolio_accepted_digest(uuid) is
  'Deterministic sha256 a partner accepts, covering the graph and every child binding.';

-- ---------------------------------------------------------------------------
-- Structural validation: closed and acyclic in BOTH structures, the parent
-- hierarchy and the dependency set.
-- ---------------------------------------------------------------------------
create or replace function ops.portfolio_revision_structure_valid(p_revision_id uuid)
returns boolean language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare v_portfolio_ref text; v_nodes integer; v_children integer; v_bad integer; v_ordered integer;
begin
  select portfolio_ref into v_portfolio_ref from ops.portfolio_revision where id = p_revision_id;
  if not found then return false; end if;

  select count(*) into v_nodes from ops.portfolio_node where portfolio_revision_id = p_revision_id;
  select count(*) into v_children from ops.portfolio_child_revision where portfolio_revision_id = p_revision_id;
  if v_nodes <> 21 or v_children <> 4 then return false; end if;

  select count(*) into v_bad from ops.portfolio_node_edge e
   where e.portfolio_revision_id = p_revision_id
     and (not exists (select 1 from ops.portfolio_node n
                       where n.portfolio_revision_id = p_revision_id and n.node_ref = e.from_node_ref)
       or not exists (select 1 from ops.portfolio_node n
                       where n.portfolio_revision_id = p_revision_id and n.node_ref = e.to_node_ref));
  if v_bad > 0 then return false; end if;

  select count(*) into v_bad from ops.portfolio_node n
   where n.portfolio_revision_id = p_revision_id
     and n.parent_ref <> v_portfolio_ref
     and not exists (select 1 from ops.portfolio_node p
                      where p.portfolio_revision_id = p_revision_id and p.node_ref = n.parent_ref);
  if v_bad > 0 then return false; end if;

  -- Every node names a child this revision actually carries.
  select count(*) into v_bad from ops.portfolio_node n
   where n.portfolio_revision_id = p_revision_id
     and not exists (select 1 from ops.portfolio_child_revision c
                      where c.portfolio_revision_id = p_revision_id and c.child_ref = n.child_ref);
  if v_bad > 0 then return false; end if;

  -- The parent hierarchy terminates at the portfolio for every node; a chain
  -- that never reaches it is a parent cycle.
  with recursive walk(node_ref, current_ref, depth) as (
    select n.node_ref, n.parent_ref, 1 from ops.portfolio_node n
     where n.portfolio_revision_id = p_revision_id
    union all
    select w.node_ref, p.parent_ref, w.depth + 1
      from walk w join ops.portfolio_node p
        on p.portfolio_revision_id = p_revision_id and p.node_ref = w.current_ref
     where w.current_ref <> v_portfolio_ref and w.depth <= 21)
  select count(*) into v_bad from ops.portfolio_node n
   where n.portfolio_revision_id = p_revision_id
     and not exists (select 1 from walk w
                      where w.node_ref = n.node_ref and w.current_ref = v_portfolio_ref);
  if v_bad > 0 then return false; end if;

  -- The dependency set is acyclic: every node reaches a topological position.
  with recursive ordered(node_ref, level) as (
    select n.node_ref, 1 from ops.portfolio_node n
     where n.portfolio_revision_id = p_revision_id
       and not exists (select 1 from ops.portfolio_node_edge e
                        where e.portfolio_revision_id = p_revision_id and e.to_node_ref = n.node_ref)
    union
    select e.to_node_ref, o.level + 1
      from ordered o join ops.portfolio_node_edge e
        on e.portfolio_revision_id = p_revision_id and e.from_node_ref = o.node_ref
     where o.level <= 21)
  select count(distinct node_ref) into v_ordered from ordered;
  if v_ordered <> 21 then return false; end if;

  return true;
end;
$$;

comment on function ops.portfolio_revision_structure_valid(uuid) is
  'True when one portfolio revision has 21 nodes, four children, closed edges and child references, a parent hierarchy terminating at the portfolio, and an acyclic dependency set.';

-- A revision is only complete once its children, nodes and edges are in, so its
-- structure and both digests are checked at COMMIT. Stored digests are compared
-- against ones recomputed from the rows: a caller may supply a hash, it is
-- never taken as the answer.
create or replace function ops.portfolio_revision_complete()
returns trigger language plpgsql
set search_path = pg_catalog, ops, public
as $$
declare v_graph text; v_accepted text; v_child record;
begin
  if not ops.portfolio_revision_structure_valid(new.id) then
    raise exception 'portfolio revision % is not a closed acyclic 21-node four-child graph', new.id;
  end if;
  v_graph := ops.portfolio_graph_digest(new.id);
  if new.graph_digest <> v_graph then
    raise exception 'portfolio graph digest does not match its rows: stored %, computed %',
      new.graph_digest, v_graph;
  end if;
  for v_child in select child_ref, child_digest from ops.portfolio_child_revision
                  where portfolio_revision_id = new.id loop
    if v_child.child_digest <> ops.portfolio_child_digest(new.id, v_child.child_ref) then
      raise exception 'child % digest does not match its content: stored %, computed %',
        v_child.child_ref, v_child.child_digest, ops.portfolio_child_digest(new.id, v_child.child_ref);
    end if;
  end loop;
  v_accepted := ops.portfolio_accepted_digest(new.id);
  if new.accepted_digest <> v_accepted then
    raise exception 'portfolio accepted digest does not match its rows: stored %, computed %',
      new.accepted_digest, v_accepted;
  end if;
  if new.edge_count <> (select count(*) from ops.portfolio_node_edge
                         where portfolio_revision_id = new.id) then
    raise exception 'portfolio revision edge_count disagrees with its edges';
  end if;
  return null;
end;
$$;

comment on function ops.portfolio_revision_complete() is
  'Deferred completeness check: at commit a revision must be structurally valid and its stored graph, child and accepted digests must each equal the digest recomputed from its own rows.';

drop trigger if exists portfolio_revision_complete on ops.portfolio_revision;
create constraint trigger portfolio_revision_complete
  after insert on ops.portfolio_revision
  deferrable initially deferred
  for each row execute function ops.portfolio_revision_complete();

-- ---------------------------------------------------------------------------
-- Review and acceptance guards. Everything either act depends on is checked
-- HERE, so a handler bug cannot admit an unreviewed, stale or misattributed
-- hash.
-- ---------------------------------------------------------------------------
create or replace function ops.portfolio_review_guard()
returns trigger language plpgsql
set search_path = pg_catalog, ops, public
as $$
declare v_live text; v_proposer uuid;
begin
  select proposed_by_actor_id into v_proposer from ops.portfolio_revision
   where id = new.portfolio_revision_id;
  if not found then
    raise exception 'portfolio review names an unknown revision';
  end if;
  -- The reviewer is the server-established writer, never a payload field.
  if new.reviewer_actor_id <> ops.portfolio_writer_actor_id() then
    raise exception 'portfolio review actor does not match the authenticated writer context';
  end if;
  v_live := ops.portfolio_accepted_digest(new.portfolio_revision_id);
  if new.reviewed_digest <> v_live then
    raise exception 'portfolio review digest is stale: expected %', v_live;
  end if;
  if new.verdict = 'pass' and new.reviewer_actor_id = v_proposer then
    raise exception 'a proposer may not pass their own portfolio revision';
  end if;
  return new;
end;
$$;

comment on function ops.portfolio_review_guard() is
  'Refuses a portfolio review that is misattributed, written against a digest the revision no longer has, or a self-review pass.';

drop trigger if exists portfolio_review_guard on ops.portfolio_revision_review;
create trigger portfolio_review_guard
  before insert on ops.portfolio_revision_review
  for each row execute function ops.portfolio_review_guard();

create or replace function ops.portfolio_proposal_guard()
returns trigger language plpgsql
set search_path = pg_catalog, ops, public
as $$
begin
  if new.proposed_by_actor_id <> ops.portfolio_writer_actor_id() then
    raise exception 'portfolio proposal actor does not match the authenticated writer context';
  end if;
  return new;
end;
$$;

comment on function ops.portfolio_proposal_guard() is
  'Binds a proposal to the server-established writer, so a proposal cannot be attributed to another actor.';

drop trigger if exists portfolio_proposal_guard on ops.portfolio_revision;
create trigger portfolio_proposal_guard
  before insert on ops.portfolio_revision
  for each row execute function ops.portfolio_proposal_guard();

create or replace function ops.portfolio_acceptance_guard()
returns trigger language plpgsql
set search_path = pg_catalog, ops, public
as $$
declare
  v_rev ops.portfolio_revision%rowtype;
  v_review ops.portfolio_revision_review%rowtype;
  v_live text; v_partner text; v_partner_actor_id uuid;
begin
  select * into v_rev from ops.portfolio_revision where id = new.portfolio_revision_id;
  if not found then raise exception 'portfolio acceptance names an unknown revision'; end if;
  if new.portfolio_ref <> v_rev.portfolio_ref then
    raise exception 'portfolio acceptance names the wrong portfolio';
  end if;

  -- The digest is RECOMPUTED from the persisted rows; a caller-supplied hash is
  -- only ever compared against it, never trusted as the value.
  v_live := ops.portfolio_accepted_digest(new.portfolio_revision_id);
  if new.accepted_digest <> v_live or v_rev.accepted_digest <> v_live then
    raise exception 'portfolio acceptance digest is stale: expected %', v_live;
  end if;
  if not ops.portfolio_revision_structure_valid(new.portfolio_revision_id) then
    raise exception 'portfolio revision structure is invalid';
  end if;

  select * into v_review from ops.portfolio_revision_review where id = new.review_id;
  if not found then raise exception 'portfolio acceptance names an unknown review'; end if;
  if v_review.portfolio_revision_id <> new.portfolio_revision_id then
    raise exception 'portfolio acceptance names a review of a different revision';
  end if;
  if v_review.verdict <> 'pass' then
    raise exception 'portfolio acceptance requires a passing review';
  end if;
  if v_review.reviewed_digest <> v_live then
    raise exception 'the passing review was written against different bytes';
  end if;
  if v_review.reviewer_actor_id = v_rev.proposed_by_actor_id then
    raise exception 'the reviewer may not be the proposer of the same revision';
  end if;

  -- THE TRUST BOUNDARY. An actor id in the payload proves nothing: anyone able
  -- to insert could name Joe. What authenticates the acceptor is the database
  -- session -- the per-partner authority credential the server selects from
  -- verified session state. ops.authority_actor_slug() reads session_user and
  -- raises for any other principal, so a supplied id may only agree with it.
  v_partner := ops.authority_actor_slug();
  select id into v_partner_actor_id from public.actor
   where slug = v_partner and active and kind = 'human';
  if not found then
    raise exception 'partner authority session % has no active human actor', v_partner;
  end if;
  if new.accepted_by_actor_id <> v_partner_actor_id then
    raise exception 'portfolio acceptance actor does not match the authenticated partner session';
  end if;

  -- Three distinct roles, not two: a reviewer who can accept their own pass is
  -- not an independent reviewer.
  if new.accepted_by_actor_id = v_rev.proposed_by_actor_id then
    raise exception 'the acceptor may not be the proposer of the same revision';
  end if;
  if new.accepted_by_actor_id = v_review.reviewer_actor_id then
    raise exception 'the acceptor may not also be the independent reviewer';
  end if;

  return new;
end;
$$;

comment on function ops.portfolio_acceptance_guard() is
  'Authoritative acceptance precondition: recomputed accepted digest, valid structure, a fresh passing independent review on the same bytes, three distinct identities, and an acceptor derived from the authenticated partner session.';

drop trigger if exists portfolio_acceptance_guard on ops.portfolio_revision_acceptance_receipt;
create trigger portfolio_acceptance_guard
  before insert on ops.portfolio_revision_acceptance_receipt
  for each row execute function ops.portfolio_acceptance_guard();

-- ---------------------------------------------------------------------------
-- The accepted ancestor, and the descendant binding the existing Engineering
-- admission consults. The route is derived from TRUSTED STORED SOURCE: whether
-- work is portfolio-governed is answered by the accepted portfolio, never by a
-- caller flag.
-- ---------------------------------------------------------------------------
-- The current accepted identity, chosen WITHOUT looking at integrity. Which
-- revision is current is a fact about acceptance; whether it is intact is a
-- separate question asked next. Deciding them together is what let a tampered
-- current revision disappear behind an older healthy one.
create or replace function ops.portfolio_current_accepted_revision(p_portfolio_ref text)
returns uuid language sql stable security definer
set search_path = pg_catalog, ops, public
as $$
  select r.id from ops.portfolio_revision r
    join ops.portfolio_revision_acceptance_receipt a on a.portfolio_revision_id = r.id
   where r.portfolio_ref = p_portfolio_ref
   order by r.revision_version desc limit 1
$$;

comment on function ops.portfolio_current_accepted_revision(text) is
  'The highest-version revision of one portfolio that carries an acceptance receipt, regardless of integrity.';

-- Why one revision is not trustworthy, or null when it is. Every clause is a
-- recomputation from the persisted rows, so a tampered row cannot answer for
-- itself.
create or replace function ops.portfolio_revision_integrity_error(p_revision_id uuid)
returns text language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare v_rev ops.portfolio_revision%rowtype; v_receipt ops.portfolio_revision_acceptance_receipt%rowtype;
        v_live text; v_child record;
begin
  select * into v_rev from ops.portfolio_revision where id = p_revision_id;
  if not found then return format('revision %s does not exist', p_revision_id); end if;
  if not ops.portfolio_revision_structure_valid(p_revision_id) then
    return format('revision %s is no longer a closed acyclic 21-node four-child graph', p_revision_id);
  end if;
  if v_rev.graph_digest <> ops.portfolio_graph_digest(p_revision_id) then
    return format('revision %s graph digest no longer matches its rows', p_revision_id);
  end if;
  for v_child in select child_ref, child_digest from ops.portfolio_child_revision
                  where portfolio_revision_id = p_revision_id loop
    if v_child.child_digest <> ops.portfolio_child_digest(p_revision_id, v_child.child_ref) then
      return format('revision %s child %s digest no longer matches its content',
                    p_revision_id, v_child.child_ref);
    end if;
  end loop;
  v_live := ops.portfolio_accepted_digest(p_revision_id);
  if v_rev.accepted_digest <> v_live then
    return format('revision %s accepted digest no longer matches its rows', p_revision_id);
  end if;
  select * into v_receipt from ops.portfolio_revision_acceptance_receipt
   where portfolio_revision_id = p_revision_id;
  if found and v_receipt.accepted_digest <> v_live then
    return format('revision %s acceptance receipt names a digest the rows no longer produce', p_revision_id);
  end if;
  return null;
end;
$$;

comment on function ops.portfolio_revision_integrity_error(uuid) is
  'Why one portfolio revision is not trustworthy, recomputed from its rows, or null when it is intact.';

-- The accepted ancestor. It selects the current accepted identity first and
-- then REFUSES an integrity failure. It never falls back to an older healthy
-- revision and never reports a tampered portfolio as no portfolio: both would
-- turn corruption into ordinary source, which is the fail-open shape this guard
-- exists to prevent.
create or replace function ops.portfolio_accepted_revision(p_portfolio_ref text)
returns uuid language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare v_id uuid; v_error text;
begin
  v_id := ops.portfolio_current_accepted_revision(p_portfolio_ref);
  if v_id is null then return null; end if;
  v_error := ops.portfolio_revision_integrity_error(v_id);
  if v_error is not null then
    raise exception 'accepted portfolio % failed integrity: %', p_portfolio_ref, v_error
      using errcode = 'integrity_constraint_violation';
  end if;
  return v_id;
end;
$$;

comment on function ops.portfolio_accepted_revision(text) is
  'The current accepted portfolio revision. Null when the portfolio has no acceptance at all; an explicit refusal when the current accepted revision fails integrity.';

create or replace function ops.portfolio_descendant_binding(p_slice_ref text)
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_node ops.portfolio_node%rowtype;
  v_revision_id uuid; v_portfolio_ref text;
  v_child ops.portfolio_child_revision%rowtype;
  v_plan_ref text; v_predecessors jsonb;
  v_accepted record; v_error text;
  v_refs text[]; v_ids uuid[];
begin
  -- INTEGRITY BEFORE DISCOVERY. Looking the node up first made governance
  -- depend on the very rows corruption can edit: delete or rename a node in the
  -- accepted revision and the lookup simply misses, and a missing row read as
  -- "no portfolio names this" is the fail-open answer. So every current accepted
  -- revision is verified FIRST, and a failure refuses outright -- a portfolio
  -- whose rows no longer produce its accepted digest might be exactly the one
  -- that names this slice, so no honest answer about any slice is available
  -- while one is unreadable.
  for v_accepted in
    select r.portfolio_ref, r.id
      from ops.portfolio_revision r
     where r.id = ops.portfolio_current_accepted_revision(r.portfolio_ref)
  loop
    v_error := ops.portfolio_revision_integrity_error(v_accepted.id);
    if v_error is not null then
      raise exception 'accepted portfolio % failed integrity: %', v_accepted.portfolio_ref, v_error
        using errcode = 'integrity_constraint_violation';
    end if;
  end loop;

  -- Only a VERIFIED current accepted revision can govern. A proposal nobody
  -- accepted governs nothing and blocks nothing, so an arbitrary proposal
  -- naming a slice can never blanket-block ordinary work.
  --
  -- AN AMBIGUOUS ANCESTOR IS REFUSED, NEVER RANKED. Node reference uniqueness
  -- is enforced per revision, so two different portfolios may each hold a
  -- current accepted revision naming this same slice. Both can pass integrity
  -- and both can bind the same child plan while carrying different budget,
  -- authority, model-floor or terminal metadata -- and every one of those
  -- fields rides into the envelope. Taking the first row would let an ordering
  -- decide which portfolio governs, so the collision is a genuine contradiction
  -- about governance that only a human can settle. Refusing keeps that visible
  -- instead of resolving it silently in whichever direction the plan happened
  -- to scan.
  select array_agg(r.portfolio_ref order by r.portfolio_ref collate "C"),
         array_agg(r.id order by r.portfolio_ref collate "C")
    into v_refs, v_ids
    from ops.portfolio_node n
    join ops.portfolio_revision r on r.id = n.portfolio_revision_id
   where n.node_ref = p_slice_ref
     and r.id = ops.portfolio_current_accepted_revision(r.portfolio_ref);

  if v_refs is null then
    -- Every accepted portfolio is intact and none names this slice: genuine
    -- ordinary attended source work, which this route leaves alone.
    return jsonb_build_object('governed', false);
  end if;
  if array_length(v_refs, 1) > 1 then
    raise exception
      'slice % is named by % current accepted portfolios (%); an ambiguous governing ancestor is refused, never ranked',
      p_slice_ref, array_length(v_refs, 1), array_to_string(v_refs, ', ')
      using errcode = 'integrity_constraint_violation';
  end if;
  v_portfolio_ref := v_refs[1];
  v_revision_id := v_ids[1];

  select * into v_node from ops.portfolio_node
   where portfolio_revision_id = v_revision_id and node_ref = p_slice_ref;
  select * into v_child from ops.portfolio_child_revision
   where portfolio_revision_id = v_revision_id and child_ref = v_node.child_ref;
  if not found then
    raise exception 'accepted portfolio revision % has no child % for node %',
      v_revision_id, v_node.child_ref, p_slice_ref;
  end if;
  select p.plan_ref into v_plan_ref from ops.sourced_work_request_plan p
   where p.id = v_child.accepted_plan_id;

  -- Each predecessor carries the child and accepted plan that actually govern
  -- IT. Proof for a same-named slice under a different plan is not proof of
  -- this predecessor, so the consumer needs the predecessor's own binding
  -- rather than the current one.
  select coalesce(jsonb_agg(jsonb_build_object(
             'node_ref', e.from_node_ref,
             'child_ref', pn.child_ref,
             'accepted_plan_ref', coalesce(to_jsonb(pp.plan_ref), 'null'::jsonb))
           order by e.from_node_ref collate "C"), '[]'::jsonb)
    into v_predecessors
    from ops.portfolio_node_edge e
    join ops.portfolio_node pn
      on pn.portfolio_revision_id = v_revision_id and pn.node_ref = e.from_node_ref
    left join ops.portfolio_child_revision pc
      on pc.portfolio_revision_id = v_revision_id and pc.child_ref = pn.child_ref
    left join ops.sourced_work_request_plan pp on pp.id = pc.accepted_plan_id
   where e.portfolio_revision_id = v_revision_id and e.to_node_ref = p_slice_ref;

  -- Every field returned is inside the accepted digest. Nothing outside it is
  -- exposed here, so an admission decision can never rest on unaccepted data.
  return jsonb_build_object(
    'governed', true,
    'portfolio_ref', v_portfolio_ref,
    'portfolio_revision_id', v_revision_id,
    'accepted_digest', ops.portfolio_accepted_digest(v_revision_id),
    'child_ref', v_child.child_ref,
    'child_version', v_child.child_version,
    'child_digest', v_child.child_digest,
    'child_accepted_plan_ref', coalesce(to_jsonb(v_plan_ref), 'null'::jsonb),
    'node_ref', v_node.node_ref,
    'parent_ref', v_node.parent_ref,
    'authority_class', v_node.authority_class,
    'effect_class', v_node.effect_class,
    'data_class', v_node.data_class,
    'budget_identity', v_node.budget_identity,
    'budget_ceiling', v_node.budget_ceiling,
    'model_floor', v_node.model_floor,
    'recovery_ref', v_node.recovery_ref,
    'terminal_predicate', v_node.terminal_predicate,
    'predecessors', v_predecessors);
end;
$$;

comment on function ops.portfolio_descendant_binding(text) is
  'Ancestor and predecessor binding for one slice reference, derived from the accepted portfolio and exposing only fields inside the accepted digest. Returns governed=false for a slice no accepted portfolio names.';

create or replace function ops.portfolio_readback(p_portfolio_ref text)
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare v_rev ops.portfolio_revision%rowtype; v_accepted_id uuid;
begin
  select * into v_rev from ops.portfolio_revision
   where portfolio_ref = p_portfolio_ref order by revision_version desc limit 1;
  if not found then
    return jsonb_build_object('portfolio_ref', p_portfolio_ref, 'exists', false);
  end if;
  v_accepted_id := ops.portfolio_accepted_revision(p_portfolio_ref);

  return jsonb_build_object(
    'portfolio_ref', v_rev.portfolio_ref,
    'exists', true,
    'schema_version', v_rev.schema_version,
    'accepted_schema_version', v_rev.accepted_schema_version,
    'revision_version', v_rev.revision_version,
    'graph_digest', ops.portfolio_graph_digest(v_rev.id),
    'accepted_digest', ops.portfolio_accepted_digest(v_rev.id),
    'stored_accepted_digest', v_rev.accepted_digest,
    'structure_valid', ops.portfolio_revision_structure_valid(v_rev.id),
    'node_count', v_rev.node_count,
    'edge_count', v_rev.edge_count,
    'child_bindings', ops.portfolio_accepted_preimage(v_rev.id) -> 'child_bindings',
    'reviews', (select coalesce(jsonb_agg(jsonb_build_object(
                         'verdict', rv.verdict, 'reviewed_digest', rv.reviewed_digest,
                         'reviewer_actor_id', rv.reviewer_actor_id) order by rv.created_at), '[]'::jsonb)
                  from ops.portfolio_revision_review rv where rv.portfolio_revision_id = v_rev.id),
    'accepted', v_accepted_id is not null and v_accepted_id = v_rev.id,
    'accepted_revision_id', v_accepted_id,
    -- Inert by construction: nothing here can produce an effect, and acceptance
    -- remains a separate human exact-hash act.
    'effects', jsonb_build_object(
      'creates_effect', false, 'jobs', 0, 'capabilities', 0, 'execution_envelopes', 0,
      'admissions', 0, 'schedules', 0, 'deployments', 0));
end;
$$;

comment on function ops.portfolio_readback(text) is
  'Deterministic zero-effect readback of one portfolio, exposing only content inside its accepted digest.';

-- ---------------------------------------------------------------------------
-- Grants. Reads reach the ordinary bundles. DIRECT INSERT IS GRANTED TO NOBODY:
-- every write goes through a definer function that derives its own actor, so a
-- writer holding a raw connection cannot attribute a row to someone else.
-- ---------------------------------------------------------------------------
grant select on ops.portfolio_revision, ops.portfolio_child_revision,
  ops.portfolio_node, ops.portfolio_node_edge, ops.portfolio_revision_review,
  ops.portfolio_revision_acceptance_receipt to carr_reader, carr_writer, carr_authority;

revoke insert, update, delete, truncate on ops.portfolio_revision,
  ops.portfolio_child_revision, ops.portfolio_node, ops.portfolio_node_edge,
  ops.portfolio_revision_review, ops.portfolio_revision_acceptance_receipt
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;

revoke all on function ops.portfolio_writer_actor_id(),
  ops.portfolio_canonical_number_text(numeric), ops.portfolio_canonical_json(jsonb),
  ops.portfolio_canonical_number_valid(numeric), ops.portfolio_model_floor_valid(jsonb),
  ops.portfolio_graph_preimage(uuid), ops.portfolio_graph_digest(uuid),
  ops.portfolio_child_preimage(uuid,text), ops.portfolio_child_digest(uuid,text),
  ops.portfolio_accepted_preimage(uuid), ops.portfolio_accepted_digest(uuid),
  ops.portfolio_revision_structure_valid(uuid), ops.portfolio_accepted_revision(text),
  ops.portfolio_current_accepted_revision(text), ops.portfolio_revision_integrity_error(uuid),
  ops.portfolio_descendant_binding(text), ops.portfolio_readback(text)
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;
grant execute on function ops.portfolio_writer_actor_id(),
  ops.portfolio_canonical_number_text(numeric), ops.portfolio_canonical_json(jsonb),
  ops.portfolio_canonical_number_valid(numeric), ops.portfolio_model_floor_valid(jsonb),
  ops.portfolio_graph_preimage(uuid), ops.portfolio_graph_digest(uuid),
  ops.portfolio_child_preimage(uuid,text), ops.portfolio_child_digest(uuid,text),
  ops.portfolio_accepted_preimage(uuid), ops.portfolio_accepted_digest(uuid),
  ops.portfolio_revision_structure_valid(uuid), ops.portfolio_accepted_revision(text),
  ops.portfolio_current_accepted_revision(text), ops.portfolio_revision_integrity_error(uuid),
  ops.portfolio_descendant_binding(text), ops.portfolio_readback(text)
  to carr_reader, carr_writer, carr_jobs, carr_authority;

-- ---------------------------------------------------------------------------
-- The only write path. Each function derives its own actor and accepts none.
-- ---------------------------------------------------------------------------
create or replace function ops.portfolio_propose_revision(
  p_portfolio_ref text, p_revision_version integer, p_idempotency_key uuid,
  p_source_digests jsonb, p_graph_digest text, p_accepted_digest text,
  p_children jsonb, p_nodes jsonb, p_edges jsonb)
returns uuid language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare v_rev uuid; v_actor uuid; c jsonb; n jsonb; e jsonb; v_plan_id uuid;
begin
  v_actor := ops.portfolio_writer_actor_id();
  insert into ops.portfolio_revision(
    portfolio_ref, revision_version, idempotency_key, schema_version,
    accepted_schema_version, preimage, graph_digest, accepted_digest,
    node_count, edge_count, child_count, proposed_by_actor_id)
  values (p_portfolio_ref, p_revision_version, p_idempotency_key,
    'doctorcre-v5-portfolio-revision.v1', 'doctorcre-v5-portfolio-accepted-revision.v1',
    jsonb_build_object('source_digests', p_source_digests), p_graph_digest, p_accepted_digest,
    jsonb_array_length(p_nodes), jsonb_array_length(p_edges), jsonb_array_length(p_children),
    v_actor)
  returning id into v_rev;

  for c in select value from jsonb_array_elements(p_children) loop
    v_plan_id := null;
    if jsonb_typeof(c -> 'accepted_plan_ref') = 'string' then
      -- ACCEPTED means accepted. A sourced plan row is only a proposal until a
      -- human acceptance receipt exists for it, and binding a child to a merely
      -- proposed plan would let an unaccepted plan inherit governing authority
      -- through the portfolio. The join to the receipt is the whole check.
      select p.id into v_plan_id
        from ops.sourced_work_request_plan p
        join ops.sourced_work_request_plan_acceptance_receipt a on a.plan_id = p.id
       where p.plan_ref = c ->> 'accepted_plan_ref'
         and a.plan_hash = p.plan_hash;
      if not found then
        raise exception 'child % names accepted_plan_ref %, which is not an accepted plan: no acceptance receipt binds that exact plan hash',
          c ->> 'child_ref', c ->> 'accepted_plan_ref';
      end if;
    end if;
    insert into ops.portfolio_child_revision(
      portfolio_revision_id, child_ref, child_ordinal, child_version, child_digest, accepted_plan_id)
    values (v_rev, c ->> 'child_ref', (c ->> 'child_ordinal')::integer,
      (c ->> 'child_version')::integer, c ->> 'child_digest', v_plan_id);
  end loop;

  for n in select value from jsonb_array_elements(p_nodes) loop
    insert into ops.portfolio_node(
      portfolio_revision_id, node_ref, node_kind, ordinal, parent_ref, child_ref,
      authority_class, effect_class, data_class, budget_identity, budget_ceiling,
      model_floor, recovery_ref, terminal_predicate)
    values (v_rev, n ->> 'node_ref', n ->> 'node_kind', (n ->> 'ordinal')::integer,
      n ->> 'parent_ref', n ->> 'child_ref', n ->> 'authority_class', n ->> 'effect_class',
      n ->> 'data_class', n ->> 'budget_identity', (n ->> 'budget_ceiling')::numeric,
      n -> 'model_floor', n ->> 'recovery_ref', n ->> 'terminal_predicate');
  end loop;

  for e in select value from jsonb_array_elements(p_edges) loop
    insert into ops.portfolio_node_edge(portfolio_revision_id, from_node_ref, to_node_ref)
    values (v_rev, e ->> 'from_node_ref', e ->> 'to_node_ref');
  end loop;

  return v_rev;
end;
$$;

comment on function ops.portfolio_propose_revision(text,integer,uuid,jsonb,text,text,jsonb,jsonb,jsonb) is
  'The only way to create a portfolio revision. Proposal is inert; the proposer is derived from the server-established writer context and is not a parameter.';

create or replace function ops.portfolio_review_revision(
  p_revision_id uuid, p_idempotency_key uuid, p_reviewed_digest text,
  p_verdict text, p_review_summary text)
returns uuid language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare v_id uuid;
begin
  insert into ops.portfolio_revision_review(
    portfolio_revision_id, idempotency_key, reviewed_digest, verdict,
    review_summary, reviewer_actor_id)
  values (p_revision_id, p_idempotency_key, p_reviewed_digest, p_verdict,
    p_review_summary, ops.portfolio_writer_actor_id())
  returning id into v_id;
  return v_id;
end;
$$;

comment on function ops.portfolio_review_revision(uuid,uuid,text,text,text) is
  'The only way to record an independent portfolio review. The reviewer is derived from the server-established writer context and is not a parameter.';

create or replace function ops.portfolio_accept_revision(
  p_revision_id uuid, p_idempotency_key uuid, p_accepted_digest text, p_review_id uuid)
returns uuid language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare v_id uuid; v_partner text; v_actor uuid; v_portfolio_ref text;
begin
  -- Raises unless session_user is an admitted partner authority principal.
  v_partner := ops.authority_actor_slug();
  select id into v_actor from public.actor where slug = v_partner and active and kind = 'human';
  if not found then
    raise exception 'partner authority session % has no active human actor', v_partner;
  end if;
  select portfolio_ref into v_portfolio_ref from ops.portfolio_revision where id = p_revision_id;
  if not found then raise exception 'portfolio acceptance names an unknown revision'; end if;
  insert into ops.portfolio_revision_acceptance_receipt(
    portfolio_revision_id, portfolio_ref, idempotency_key, accepted_digest,
    review_id, accepted_by_actor_id)
  values (p_revision_id, v_portfolio_ref, p_idempotency_key, p_accepted_digest,
    p_review_id, v_actor)
  returning id into v_id;
  return v_id;
end;
$$;

comment on function ops.portfolio_accept_revision(uuid,uuid,text,uuid) is
  'The only way to accept a portfolio revision. The acceptor is derived from the authenticated partner authority session and is not a parameter.';

revoke all on function
  ops.portfolio_propose_revision(text,integer,uuid,jsonb,text,text,jsonb,jsonb,jsonb),
  ops.portfolio_review_revision(uuid,uuid,text,text,text),
  ops.portfolio_accept_revision(uuid,uuid,text,uuid)
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;
grant execute on function
  ops.portfolio_propose_revision(text,integer,uuid,jsonb,text,text,jsonb,jsonb,jsonb),
  ops.portfolio_review_revision(uuid,uuid,text,text,text)
  to carr_writer, carr_authority;
-- Acceptance reaches the authority bundle only.
grant execute on function ops.portfolio_accept_revision(uuid,uuid,text,uuid) to carr_authority;

`;

// Forward successor for the DoctorCRE v5 portfolio hierarchy. Unlike the
// registry-only successors before it this migration DOES carry domain DDL: the
// append-only portfolio tables, their guards and their closed definer write
// path. The domain SQL runs first, moving the security-definer catalog, and the
// v22 successor then seals the catalog the domain produced -- which is why the
// two cannot be separate migrations.
export function renderDoctorcrePortfolioForwardRegistrySql(rows = fullInventory(),
  dbCatalogBaseline = DOCTORCRE_PORTFOLIO_FORWARD_DB_CATALOG_BASELINE,
  predecessorArtifacts = undefined) {
  // The predecessor seal, its catalog projection and the sealed v21 artifact
  // hashes are fixed production constants, asserted by the shared v22 trust
  // root that every v22 entry path calls. There is deliberately no caller
  // binding for them: a supplied predecessor artifact is checked AGAINST these
  // pins, it never supplies its own expected hash.
  assertDoctorcrePortfolioV22TrustRoot();
  const { v21: v21Seal } = HISTORICAL_REGISTRY_SEALS;
  const predecessorDbCatalogBaseline = DOCTORCRE_PORTFOLIO_PRE_V22_DB_CATALOG_BASELINE;
  const artifactShaRe = CONTINUITY_ARCHIVE_ARTIFACT_SHA_RE;
  // A caller-supplied successor baseline is held to the SAME exact-projection
  // shape as the fixed constant; it can only ever narrow, never widen.
  assertR06HooksCorrectnessCatalogBaseline("successor v22", dbCatalogBaseline,
    "scac-db-catalog-projection.v22");

  const v22Digest = registryDigestFor(REGISTRY_V22_VERSION, rows, dbCatalogBaseline);
  const catalogCount = dbCatalogBaseline.secdef_execute.count +
    dbCatalogBaseline.relation_dml.count + dbCatalogBaseline.column_dml.count;
  const entryCount = rows.length + catalogCount;
  const v21MigrationPath = DOCTORCRE_PORTFOLIO_V21_MIGRATION_PATH;
  const v21RuntimePath = DOCTORCRE_PORTFOLIO_V21_RUNTIME_PATH;
  const v21Rows = frozenInventory(REGISTRY_V21_VERSION);
  // A partial predecessor bundle regenerates only the missing half, and it
  // regenerates it from the canonical prior inputs: the v21 renderer's third
  // argument is its own v20-shaped predecessor bundle, so this v21-shaped one
  // is never forwarded into it.
  const v21Migration = predecessorArtifacts?.migration ??
    renderR06HooksCorrectnessForwardRegistrySql(v21Rows, predecessorDbCatalogBaseline);
  const v21Runtime = predecessorArtifacts?.runtime ?? renderRuntimeProjection(v21Rows, {
    version: REGISTRY_V21_VERSION,
    dbCatalogBaseline: predecessorDbCatalogBaseline,
  });
  for (const [path, source] of [
    [v21MigrationPath, v21Migration], [v21RuntimePath, v21Runtime],
  ]) {
    const expected = HISTORICAL_REGISTRY_ARTIFACT_SHA256[path];
    if (!artifactShaRe.test(expected ?? ""))
      throw new Error(`DoctorCRE portfolio v22 predecessor artifact pin is unbound: ${path}`);
    const observed = sha256(source);
    if (observed !== expected)
      throw new Error(`sealed historical SCAC v21 artifact changed: ${path}: ${observed}`);
  }

  const headerMarker =
    "-- SCAC-12: registry-only mutation registry v21 after R06 hook-correctness evidence routing.";
  const coreStart = v21Migration.indexOf(headerMarker);
  if (coreStart < 0 || v21Migration.indexOf(headerMarker, coreStart + headerMarker.length) >= 0)
    throw new Error("sealed SCAC v21 migration has no exact successor core boundary");
  const v21Core = v21Migration.slice(coreStart);
  const currentV21Marker = "create or replace function ops.scac_mutation_catalog_v21_current()";
  const policyMarker =
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v20;";
  const currentV21Start = v21Core.indexOf(currentV21Marker);
  const secondCurrentV21 = v21Core.indexOf(
    currentV21Marker, currentV21Start + currentV21Marker.length);
  const v20HistoryMarker =
    "alter function ops.scac_mutation_catalog_v20_current() rename to scac_mutation_catalog_v20_live_at_seal;";
  const v20HistoryStart = v21Core.indexOf(v20HistoryMarker);
  const secondV20History = v21Core.indexOf(
    v20HistoryMarker, v20HistoryStart + v20HistoryMarker.length);
  const policyStart = v21Core.indexOf(policyMarker);
  const secondPolicy = v21Core.indexOf(policyMarker, policyStart + policyMarker.length);
  if (v20HistoryStart < 0 || secondV20History >= 0 || currentV21Start <= v20HistoryStart ||
      secondCurrentV21 >= 0 || policyStart <= currentV21Start || secondPolicy >= 0)
    throw new Error("sealed SCAC v21 migration has no exact catalog successor boundary");
  const installedV20History = v21Core.slice(v20HistoryStart, currentV21Start);
  const v21Current = v21Core.slice(currentV21Start, policyStart);
  const v21History =
`alter function ops.scac_mutation_catalog_v21_current() rename to scac_mutation_catalog_v21_live_at_seal;
create or replace function ops.scac_mutation_registry_v21_seal_available()
returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$
  select ops.scac_mutation_registry_seal_valid('scac-mutation-registry.v21')
$fn$;
create or replace function ops.scac_mutation_catalog_v21_current()
returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$
  select ops.scac_mutation_catalog_v21_live_at_seal()
$fn$;
comment on function ops.scac_mutation_registry_v21_seal_available() is 'Exact immutable v21 registry seal; separate from whether the live catalog still equals v21.';
comment on function ops.scac_mutation_catalog_v21_current() is 'Historical v21 live-catalog validator; expected to become false after the v22 authority surface is installed.';

`;
  const renderV22Current = baseline => {
    let current = v21Current
      .replaceAll("scac_mutation_catalog_v21_current", "scac_mutation_catalog_v22_current")
      .replaceAll("scac-mutation-registry.v21", "scac-mutation-registry.v22");
    for (const [category, label] of [
      ["secdef_execute", "security-definer"],
      ["relation_dml", "relation"],
      ["column_dml", "column"],
    ]) {
      current = replaceExactlyOnce(current,
        `if observed_count<>${predecessorDbCatalogBaseline[category].count} or observed_digest<>'${predecessorDbCatalogBaseline[category].digest}' then return false; end if;`,
        `if observed_count<>${baseline[category].count} or observed_digest<>'${baseline[category].digest}' then return false; end if;`,
        `DoctorCRE portfolio v22 ${label} baseline`);
    }
    return replaceExactlyOnce(current,
      `return observed_count=${predecessorDbCatalogBaseline.role_authority.count} and observed_digest='${predecessorDbCatalogBaseline.role_authority.digest}';`,
      `return observed_count=${baseline.role_authority.count} and observed_digest='${baseline.role_authority.digest}';`,
      "DoctorCRE portfolio v22 role-authority baseline");
  };
  const v22Current = renderV22Current(dbCatalogBaseline);

  let sql = replaceExactlyOnce(v21Core, v21Current,
    "__CONTINUITY_ARCHIVE_V20_CATALOG_SUCCESSOR__",
    "DoctorCRE portfolio v21 current catalog block");
  sql = replaceExactlyOnce(sql, installedV20History, "",
    "DoctorCRE portfolio already-installed v20 catalog history");
  sql = replaceExactlyOnce(sql, headerMarker,
    "-- SCAC-12: mutation registry v22 after the DoctorCRE v5 portfolio hierarchy.",
    "DoctorCRE portfolio migration header");
  sql = sql
    .replaceAll("scac-mutation-registry.v21", "scac-mutation-registry.v22")
    .replaceAll("_v21", "_v22")
    .replaceAll(" v21", " v22");
  sql = replaceExactlyOnce(sql, JSON.stringify(predecessorDbCatalogBaseline),
    JSON.stringify(dbCatalogBaseline), "DoctorCRE portfolio v22 catalog projection");
  sql = replaceExactlyOnce(sql,
    `'${v21Seal.digest}',${v21Seal.entryCount},${v21Seal.sourceEntryCount},`,
    `'sha256:${v22Digest}',${entryCount},${rows.length},`,
    "DoctorCRE portfolio v22 registry row");
  sql = replaceExactlyOnce(sql,
    `ops.scac_mutation_registration_v22('${v21Seal.digest}',`,
    `ops.scac_mutation_registration_v22('sha256:${v22Digest}',`,
    "DoctorCRE portfolio v22 snapshot registry lookup");
  sql = replaceExactlyOnce(sql,
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v20;",
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v21;",
    "DoctorCRE portfolio policy snapshot predecessor");
  sql = replaceExactlyOnce(sql, "__CONTINUITY_ARCHIVE_V20_CATALOG_SUCCESSOR__",
    `${v21History}${v22Current}`, "DoctorCRE portfolio v21 catalog history insertion");

  const versionsThrough21 = Array.from({ length: 21 }, (_, index) =>
    `'scac-mutation-registry.v${index + 1}'`).join(",");
  const versionsThrough20 = Array.from({ length: 20 }, (_, index) =>
    `'scac-mutation-registry.v${index + 1}'`).join(",");
  sql = replaceExactlyOnce(sql,
    `check (registry_version in (${versionsThrough20},'scac-mutation-registry.v22'))`,
    `check (registry_version in (${versionsThrough21},'scac-mutation-registry.v22'))`,
    "DoctorCRE portfolio registry-version constraint");
  sql = replaceExactlyOnce(sql,
    `if p_registry_version not in (${versionsThrough20}) then return false; end if;`,
    `if p_registry_version not in (${versionsThrough21}) then return false; end if;`,
    "DoctorCRE portfolio historical seal allowlist");
  sql = replaceExactlyOnce(sql,
    `    when 'scac-mutation-registry.v20' then '${HISTORICAL_REGISTRY_SEALS.v20.digest}' end;`,
    `    when 'scac-mutation-registry.v20' then '${HISTORICAL_REGISTRY_SEALS.v20.digest}'\n    when '${v21Seal.version}' then '${v21Seal.digest}' end;`,
    "DoctorCRE portfolio historical digest case");
  sql = replaceExactlyOnce(sql,
    `    when 'scac-mutation-registry.v20' then '${JSON.stringify(CONTINUITY_ARCHIVE_FORWARD_DB_CATALOG_BASELINE)}'::jsonb end;`,
    `    when 'scac-mutation-registry.v20' then '${JSON.stringify(CONTINUITY_ARCHIVE_FORWARD_DB_CATALOG_BASELINE)}'::jsonb\n    when '${v21Seal.version}' then '${JSON.stringify(predecessorDbCatalogBaseline)}'::jsonb end;`,
    "DoctorCRE portfolio historical catalog case");
  sql = replaceExactlyOnce(sql,
    `    ('scac-mutation-registry.v20','${HISTORICAL_REGISTRY_SEALS.v20.digest}',${HISTORICAL_REGISTRY_SEALS.v20.entryCount},${HISTORICAL_REGISTRY_SEALS.v20.sourceEntryCount})\n`,
    `    ('scac-mutation-registry.v20','${HISTORICAL_REGISTRY_SEALS.v20.digest}',${HISTORICAL_REGISTRY_SEALS.v20.entryCount},${HISTORICAL_REGISTRY_SEALS.v20.sourceEntryCount}),\n    ('${v21Seal.version}','${v21Seal.digest}',${v21Seal.entryCount},${v21Seal.sourceEntryCount})\n`,
    "DoctorCRE portfolio historical seal tuple");
  sql = replaceExactlyOnce(sql,
    "    ops.scac_mutation_registry_v20_seal_available()) then",
    "    ops.scac_mutation_registry_v20_seal_available() and\n    ops.scac_mutation_registry_v21_seal_available()) then",
    "DoctorCRE portfolio snapshot predecessor seal");
  sql = replaceExactlyOnce(sql,
    `or (r.registry_version='scac-mutation-registry.v22' and r.registry_digest='${v21Seal.digest}')`,
    `or (r.registry_version='scac-mutation-registry.v21' and r.registry_digest='${v21Seal.digest}')\n         or (r.registry_version='scac-mutation-registry.v22' and r.registry_digest='sha256:${v22Digest}')`,
    "DoctorCRE portfolio epoch-chain digest cases");
  sql = replaceExactlyOnce(sql,
    `  (registry_version='scac-mutation-registry.v22' and registry_digest='${v21Seal.digest}')`,
    `  (registry_version='scac-mutation-registry.v21' and registry_digest='${v21Seal.digest}') or\n  (registry_version='scac-mutation-registry.v22' and registry_digest='sha256:${v22Digest}')`,
    "DoctorCRE portfolio epoch constraint digest cases");
  sql = replaceExactlyOnce(sql,
    `'{registry_digest}',to_jsonb('${v21Seal.digest}'::text)`,
    `'{registry_digest}',to_jsonb('sha256:${v22Digest}'::text)`,
    "DoctorCRE portfolio snapshot registry digest");
  sql = replaceExactlyOnce(sql,
    "ops.scac_mutation_registry_v19_seal_available(),ops.scac_mutation_catalog_v20_live_at_seal(),ops.scac_mutation_catalog_v20_current(),ops.scac_mutation_registry_v20_seal_available(),ops.scac_mutation_catalog_v22_current()",
    "ops.scac_mutation_registry_v19_seal_available(),ops.scac_mutation_catalog_v20_live_at_seal(),ops.scac_mutation_catalog_v20_current(),ops.scac_mutation_registry_v20_seal_available(),ops.scac_mutation_catalog_v21_live_at_seal(),ops.scac_mutation_catalog_v21_current(),ops.scac_mutation_registry_v21_seal_available(),ops.scac_mutation_catalog_v22_current()",
    "DoctorCRE portfolio historical function revoke list");
  sql = replaceExactlyOnce(sql,
    // The anchor is the predecessor comment AS IT STANDS AFTER the global
    // version rename above, which has already bumped "registry v21" to v22.
    "R06 hooks-correctness successor snapshot: current policy epochs bind mutation registry v22 while historical v2/v3/v4/v5/v6/v7/v8/v9/v10/v11/v12/v13/v14/v15/v16/v17/v18/v19/v20 epochs remain immutable.",
    "DoctorCRE portfolio successor snapshot: current policy epochs bind mutation registry v22 while historical v2/v3/v4/v5/v6/v7/v8/v9/v10/v11/v12/v13/v14/v15/v16/v17/v18/v19/v20/v21 epochs remain immutable.",
    "DoctorCRE portfolio policy snapshot comment");
  sql = replaceExactlyOnce(sql,
    `(select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v22')<>${v21Seal.entryCount}`,
    `(select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v22')<>${entryCount}`,
    "DoctorCRE portfolio v22 entry count guard");
  sql = replaceExactlyOnce(sql,
    `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v20')<>'${HISTORICAL_REGISTRY_SEALS.v20.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v20')<>${HISTORICAL_REGISTRY_SEALS.v20.entryCount} then raise exception 'sealed SCAC mutation registry v20 changed during successor creation'; end if;`,
    `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v21')<>'${v21Seal.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v21')<>${v21Seal.entryCount} then raise exception 'sealed SCAC mutation registry v21 changed during successor creation'; end if;`,
    "DoctorCRE portfolio predecessor seal guard");
  sql = replaceExactlyOnce(sql,
    "ops.scac_policy_epoch_snapshot(),ops.scac_policy_epoch_snapshot_v6(),ops.scac_policy_epoch_snapshot_v7(),ops.scac_policy_epoch_snapshot_v8(),ops.scac_policy_epoch_snapshot_v9(),ops.scac_policy_epoch_snapshot_v10(),ops.scac_policy_epoch_snapshot_v11(),ops.scac_policy_epoch_snapshot_v12(),ops.scac_policy_epoch_snapshot_v13(),ops.scac_policy_epoch_snapshot_v14(),ops.scac_policy_epoch_snapshot_v15(),ops.scac_policy_epoch_snapshot_v16(),ops.scac_policy_epoch_snapshot_v17(),ops.scac_policy_epoch_snapshot_v18(),ops.scac_policy_epoch_snapshot_v19(),ops.scac_policy_epoch_snapshot_v20(),",
    "ops.scac_policy_epoch_snapshot(),ops.scac_policy_epoch_snapshot_v6(),ops.scac_policy_epoch_snapshot_v7(),ops.scac_policy_epoch_snapshot_v8(),ops.scac_policy_epoch_snapshot_v9(),ops.scac_policy_epoch_snapshot_v10(),ops.scac_policy_epoch_snapshot_v11(),ops.scac_policy_epoch_snapshot_v12(),ops.scac_policy_epoch_snapshot_v13(),ops.scac_policy_epoch_snapshot_v14(),ops.scac_policy_epoch_snapshot_v15(),ops.scac_policy_epoch_snapshot_v16(),ops.scac_policy_epoch_snapshot_v17(),ops.scac_policy_epoch_snapshot_v18(),ops.scac_policy_epoch_snapshot_v19(),ops.scac_policy_epoch_snapshot_v20(),ops.scac_policy_epoch_snapshot_v21(),",
    "DoctorCRE portfolio historical policy snapshot revoke list");

  const seedStartMarker = "with seed as (select value as contract from jsonb_array_elements(";
  const seedEndMarker = "::jsonb))\ninsert into ops.scac_mutation_registry_entry";
  const seedStart = sql.indexOf(seedStartMarker);
  const secondSeedStart = sql.indexOf(seedStartMarker, seedStart + seedStartMarker.length);
  const seedEnd = sql.indexOf(seedEndMarker, seedStart + seedStartMarker.length);
  const secondSeedEnd = sql.indexOf(seedEndMarker, seedEnd + seedEndMarker.length);
  if (seedStart < 0 || secondSeedStart >= 0 || seedEnd < 0 || secondSeedEnd >= 0)
    throw new Error("sealed SCAC v21 migration has no exact source-seed boundary");
  const seed = JSON.stringify(rows.map(row => ({ ...row, entry_digest: `sha256:${sha256(row)}` })));
  sql = `${sql.slice(0, seedStart + seedStartMarker.length)}${sqlLiteral(seed)}${sql.slice(seedEnd)}`;

  const preflightCurrent = renderV22Current(predecessorDbCatalogBaseline);
  const preflightBegin = preflightCurrent.indexOf("begin\n");
  const preflightEnd = preflightCurrent.lastIndexOf("end $fn$;");
  if (preflightBegin < 0 || preflightEnd <= preflightBegin)
    throw new Error("generated v22 catalog predicate has no exact preflight body boundary");
  let preflightBody = preflightCurrent.slice(preflightBegin + "begin\n".length, preflightEnd);
  preflightBody = preflightBody.replaceAll(
    "then return false; end if;",
    "then raise exception 'DoctorCRE portfolio pre-v22 catalog receipt drifted'; end if;");
  preflightBody = replaceExactlyOnce(preflightBody,
    `return observed_count=${predecessorDbCatalogBaseline.role_authority.count} and observed_digest='${predecessorDbCatalogBaseline.role_authority.digest}';`,
    `if observed_count<>${predecessorDbCatalogBaseline.role_authority.count} or observed_digest<>'${predecessorDbCatalogBaseline.role_authority.digest}' then raise exception 'DoctorCRE portfolio pre-v22 role-authority receipt drifted'; end if;`,
    "DoctorCRE portfolio pre-v22 role receipt");
  const predecessorHash = sha256(v21Migration);
  const predecessorPreflight =
`-- Exact disposable-Postgres post-0495 receipt. Refuse before any v22 function
-- exists. Unlike the registry-only successors before it this migration DOES
-- carry domain DDL: the append-only DoctorCRE v5 portfolio hierarchy runs
-- first and moves the security-definer catalog, and the v22 successor then
-- seals the catalog that domain SQL produced.
do $doctorcre_portfolio_preflight$
declare observed_count integer; observed_digest text; grant_snapshot jsonb;
begin
  if (select count(*) from public.schema_migrations where filename='0495_r06_hooks_correctness_scac_successor.sql')<>1
     or not exists(select 1 from public.schema_migrations where filename='0495_r06_hooks_correctness_scac_successor.sql'
       and sha256='${predecessorHash}') then
    raise exception 'DoctorCRE portfolio pre-v22 migration ledger receipt drifted';
  end if;
  grant_snapshot:=ops.scac_runtime_dml_grant_snapshot();
  if (grant_snapshot->>'entry_count')::integer<>${predecessorDbCatalogBaseline.runtime_dml_grants.count}
     or grant_snapshot->>'grant_digest'<>'${predecessorDbCatalogBaseline.runtime_dml_grants.digest}' then
    raise exception 'DoctorCRE portfolio pre-v22 runtime grant receipt drifted';
  end if;
${preflightBody}end $doctorcre_portfolio_preflight$;

`;
  return `${predecessorPreflight}${DOCTORCRE_PORTFOLIO_DOMAIN_SQL}${sql}`.replace(/\n+$/, "\n");
}

// Shared v23 trust root. Every entry path that renders or writes a v23
// artifact calls this BEFORE doing work, so an unbound template constant can
// never reach a digest, a projection or a written file.
const R07_REPO_HYGIENE_JANITOR_V22_MIGRATION_PATH =
  "migrations/0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql";
const R07_REPO_HYGIENE_JANITOR_V22_RUNTIME_PATH =
  "mcp-server/src/scac-mutation-registry.v22.generated.js";

export function assertR07RepoHygieneJanitorV23TrustRoot() {
  // Strictly stronger than the root it succeeds: the whole v22 chain has to be
  // bound before a v23 artifact can exist at all.
  assertDoctorcrePortfolioV22TrustRoot();
  const { v22: v22Seal } = HISTORICAL_REGISTRY_SEALS;
  if (v22Seal?.version !== REGISTRY_V22_VERSION ||
      !CONTINUITY_ARCHIVE_DIGEST_RE.test(v22Seal?.digest ?? "") ||
      !Number.isInteger(v22Seal?.entryCount) || v22Seal.entryCount < 1 ||
      !Number.isInteger(v22Seal?.sourceEntryCount) || v22Seal.sourceEntryCount < 1)
    throw new Error("R07 repo hygiene janitor v23 predecessor seal is unbound");
  assertR06HooksCorrectnessCatalogBaseline("predecessor v22",
    R07_REPO_HYGIENE_JANITOR_PRE_V23_DB_CATALOG_BASELINE, "scac-db-catalog-projection.v22");
  assertR06HooksCorrectnessCatalogBaseline("successor v23",
    R07_REPO_HYGIENE_JANITOR_FORWARD_DB_CATALOG_BASELINE, "scac-db-catalog-projection.v23");
  for (const path of [
    R07_REPO_HYGIENE_JANITOR_V22_MIGRATION_PATH, R07_REPO_HYGIENE_JANITOR_V22_RUNTIME_PATH,
  ]) {
    if (!CONTINUITY_ARCHIVE_ARTIFACT_SHA_RE.test(
      HISTORICAL_REGISTRY_ARTIFACT_SHA256[path] ?? ""))
      throw new Error(`R07 repo hygiene janitor v23 predecessor artifact pin is unbound: ${path}`);
  }
}

// Registry-only successor for the R07 repo-hygiene janitor. The source change
// adds one tools/ planner script and one DELIBERATELY UNINSTALLED LaunchAgent
// definition, and re-digests the two entrypoints that carry them, so this
// migration creates no table, no role and no domain function. It only seals the
// new source inventory and installs the v23 catalog/policy projection after the
// immutable v22 frontier -- the same shape as the v20 and v21 registry-only
// successors, and unlike v22, which carried the portfolio's domain DDL.
export function renderR07RepoHygieneJanitorForwardRegistrySql(rows = fullInventory(),
  dbCatalogBaseline = R07_REPO_HYGIENE_JANITOR_FORWARD_DB_CATALOG_BASELINE,
  predecessorArtifacts = undefined) {
  // The predecessor seal, its catalog projection and the sealed v22 artifact
  // hashes are fixed production constants, asserted by the shared v23 trust
  // root that every v23 entry path calls. There is deliberately no caller
  // binding for them: a supplied predecessor artifact is checked AGAINST these
  // pins, it never supplies its own expected hash.
  assertR07RepoHygieneJanitorV23TrustRoot();
  const { v22: v22Seal } = HISTORICAL_REGISTRY_SEALS;
  const predecessorDbCatalogBaseline = R07_REPO_HYGIENE_JANITOR_PRE_V23_DB_CATALOG_BASELINE;
  const artifactShaRe = CONTINUITY_ARCHIVE_ARTIFACT_SHA_RE;
  // A caller-supplied successor baseline is held to the SAME exact-projection
  // shape as the fixed constant; it can only ever narrow, never widen.
  assertR06HooksCorrectnessCatalogBaseline("successor v23", dbCatalogBaseline,
    "scac-db-catalog-projection.v23");

  const v23Digest = registryDigestFor(REGISTRY_V23_VERSION, rows, dbCatalogBaseline);
  const catalogCount = dbCatalogBaseline.secdef_execute.count +
    dbCatalogBaseline.relation_dml.count + dbCatalogBaseline.column_dml.count;
  const entryCount = rows.length + catalogCount;
  const v22MigrationPath = R07_REPO_HYGIENE_JANITOR_V22_MIGRATION_PATH;
  const v22RuntimePath = R07_REPO_HYGIENE_JANITOR_V22_RUNTIME_PATH;
  const v22Rows = frozenInventory(REGISTRY_V22_VERSION);
  // A partial predecessor bundle regenerates only the missing half, and it
  // regenerates it from the canonical prior inputs: the v22 renderer's third
  // argument is its own v21-shaped predecessor bundle, so this v22-shaped one
  // is never forwarded into it.
  const v22Migration = predecessorArtifacts?.migration ??
    renderDoctorcrePortfolioForwardRegistrySql(v22Rows, predecessorDbCatalogBaseline);
  const v22Runtime = predecessorArtifacts?.runtime ?? renderRuntimeProjection(v22Rows, {
    version: REGISTRY_V22_VERSION,
    dbCatalogBaseline: predecessorDbCatalogBaseline,
  });
  for (const [path, source] of [
    [v22MigrationPath, v22Migration], [v22RuntimePath, v22Runtime],
  ]) {
    const expected = HISTORICAL_REGISTRY_ARTIFACT_SHA256[path];
    if (!artifactShaRe.test(expected ?? ""))
      throw new Error(`R07 repo hygiene janitor v23 predecessor artifact pin is unbound: ${path}`);
    const observed = sha256(source);
    if (observed !== expected)
      throw new Error(`sealed historical SCAC v22 artifact changed: ${path}: ${observed}`);
  }

  // Slicing from the header marker is also what drops v22's domain SQL: the
  // portfolio DDL sits BEFORE this marker, and a registry-only successor must
  // not re-emit it.
  const headerMarker =
    "-- SCAC-12: mutation registry v22 after the DoctorCRE v5 portfolio hierarchy.";
  const coreStart = v22Migration.indexOf(headerMarker);
  if (coreStart < 0 || v22Migration.indexOf(headerMarker, coreStart + headerMarker.length) >= 0)
    throw new Error("sealed SCAC v22 migration has no exact successor core boundary");
  const v22Core = v22Migration.slice(coreStart);
  const currentV22Marker = "create or replace function ops.scac_mutation_catalog_v22_current()";
  const policyMarker =
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v21;";
  const currentV22Start = v22Core.indexOf(currentV22Marker);
  const secondCurrentV22 = v22Core.indexOf(
    currentV22Marker, currentV22Start + currentV22Marker.length);
  const v21HistoryMarker =
    "alter function ops.scac_mutation_catalog_v21_current() rename to scac_mutation_catalog_v21_live_at_seal;";
  const v21HistoryStart = v22Core.indexOf(v21HistoryMarker);
  const secondV21History = v22Core.indexOf(
    v21HistoryMarker, v21HistoryStart + v21HistoryMarker.length);
  const policyStart = v22Core.indexOf(policyMarker);
  const secondPolicy = v22Core.indexOf(policyMarker, policyStart + policyMarker.length);
  if (v21HistoryStart < 0 || secondV21History >= 0 || currentV22Start <= v21HistoryStart ||
      secondCurrentV22 >= 0 || policyStart <= currentV22Start || secondPolicy >= 0)
    throw new Error("sealed SCAC v22 migration has no exact catalog successor boundary");
  const installedV21History = v22Core.slice(v21HistoryStart, currentV22Start);
  const v22Current = v22Core.slice(currentV22Start, policyStart);
  const v22History =
`alter function ops.scac_mutation_catalog_v22_current() rename to scac_mutation_catalog_v22_live_at_seal;
create or replace function ops.scac_mutation_registry_v22_seal_available()
returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$
  select ops.scac_mutation_registry_seal_valid('scac-mutation-registry.v22')
$fn$;
create or replace function ops.scac_mutation_catalog_v22_current()
returns boolean language sql stable security definer set search_path=pg_catalog,ops as $fn$
  select ops.scac_mutation_catalog_v22_live_at_seal()
$fn$;
comment on function ops.scac_mutation_registry_v22_seal_available() is 'Exact immutable v22 registry seal; separate from whether the live catalog still equals v22.';
comment on function ops.scac_mutation_catalog_v22_current() is 'Historical v22 live-catalog validator; expected to become false after the v23 authority surface is installed.';

`;
  const renderV23Current = baseline => {
    let current = v22Current
      .replaceAll("scac_mutation_catalog_v22_current", "scac_mutation_catalog_v23_current")
      .replaceAll("scac-mutation-registry.v22", "scac-mutation-registry.v23");
    for (const [category, label] of [
      ["secdef_execute", "security-definer"],
      ["relation_dml", "relation"],
      ["column_dml", "column"],
    ]) {
      current = replaceExactlyOnce(current,
        `if observed_count<>${predecessorDbCatalogBaseline[category].count} or observed_digest<>'${predecessorDbCatalogBaseline[category].digest}' then return false; end if;`,
        `if observed_count<>${baseline[category].count} or observed_digest<>'${baseline[category].digest}' then return false; end if;`,
        `R07 repo hygiene janitor v23 ${label} baseline`);
    }
    return replaceExactlyOnce(current,
      `return observed_count=${predecessorDbCatalogBaseline.role_authority.count} and observed_digest='${predecessorDbCatalogBaseline.role_authority.digest}';`,
      `return observed_count=${baseline.role_authority.count} and observed_digest='${baseline.role_authority.digest}';`,
      "R07 repo hygiene janitor v23 role-authority baseline");
  };
  const v23Current = renderV23Current(dbCatalogBaseline);

  let sql = replaceExactlyOnce(v22Core, v22Current,
    "__DOCTORCRE_PORTFOLIO_V22_CATALOG_SUCCESSOR__",
    "R07 repo hygiene janitor v22 current catalog block");
  sql = replaceExactlyOnce(sql, installedV21History, "",
    "R07 repo hygiene janitor already-installed v21 catalog history");
  sql = replaceExactlyOnce(sql, headerMarker,
    "-- SCAC-12: registry-only mutation registry v23 after the R07 repo-hygiene janitor definition.",
    "R07 repo hygiene janitor migration header");
  sql = sql
    .replaceAll("scac-mutation-registry.v22", "scac-mutation-registry.v23")
    .replaceAll("_v22", "_v23")
    .replaceAll(" v22", " v23");
  sql = replaceExactlyOnce(sql, JSON.stringify(predecessorDbCatalogBaseline),
    JSON.stringify(dbCatalogBaseline), "R07 repo hygiene janitor v23 catalog projection");
  sql = replaceExactlyOnce(sql,
    `'${v22Seal.digest}',${v22Seal.entryCount},${v22Seal.sourceEntryCount},`,
    `'sha256:${v23Digest}',${entryCount},${rows.length},`,
    "R07 repo hygiene janitor v23 registry row");
  sql = replaceExactlyOnce(sql,
    `ops.scac_mutation_registration_v23('${v22Seal.digest}',`,
    `ops.scac_mutation_registration_v23('sha256:${v23Digest}',`,
    "R07 repo hygiene janitor v23 snapshot registry lookup");
  sql = replaceExactlyOnce(sql,
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v21;",
    "alter function ops.scac_policy_epoch_snapshot() rename to scac_policy_epoch_snapshot_v22;",
    "R07 repo hygiene janitor policy snapshot predecessor");
  sql = replaceExactlyOnce(sql, "__DOCTORCRE_PORTFOLIO_V22_CATALOG_SUCCESSOR__",
    `${v22History}${v23Current}`, "R07 repo hygiene janitor v22 catalog history insertion");

  const versionsThrough22 = Array.from({ length: 22 }, (_, index) =>
    `'scac-mutation-registry.v${index + 1}'`).join(",");
  const versionsThrough21 = Array.from({ length: 21 }, (_, index) =>
    `'scac-mutation-registry.v${index + 1}'`).join(",");
  sql = replaceExactlyOnce(sql,
    `check (registry_version in (${versionsThrough21},'scac-mutation-registry.v23'))`,
    `check (registry_version in (${versionsThrough22},'scac-mutation-registry.v23'))`,
    "R07 repo hygiene janitor registry-version constraint");
  sql = replaceExactlyOnce(sql,
    `if p_registry_version not in (${versionsThrough21}) then return false; end if;`,
    `if p_registry_version not in (${versionsThrough22}) then return false; end if;`,
    "R07 repo hygiene janitor historical seal allowlist");
  sql = replaceExactlyOnce(sql,
    `    when 'scac-mutation-registry.v21' then '${HISTORICAL_REGISTRY_SEALS.v21.digest}' end;`,
    `    when 'scac-mutation-registry.v21' then '${HISTORICAL_REGISTRY_SEALS.v21.digest}'\n    when '${v22Seal.version}' then '${v22Seal.digest}' end;`,
    "R07 repo hygiene janitor historical digest case");
  sql = replaceExactlyOnce(sql,
    `    when 'scac-mutation-registry.v21' then '${JSON.stringify(R06_HOOKS_CORRECTNESS_FORWARD_DB_CATALOG_BASELINE)}'::jsonb end;`,
    `    when 'scac-mutation-registry.v21' then '${JSON.stringify(R06_HOOKS_CORRECTNESS_FORWARD_DB_CATALOG_BASELINE)}'::jsonb\n    when '${v22Seal.version}' then '${JSON.stringify(predecessorDbCatalogBaseline)}'::jsonb end;`,
    "R07 repo hygiene janitor historical catalog case");
  sql = replaceExactlyOnce(sql,
    `    ('scac-mutation-registry.v21','${HISTORICAL_REGISTRY_SEALS.v21.digest}',${HISTORICAL_REGISTRY_SEALS.v21.entryCount},${HISTORICAL_REGISTRY_SEALS.v21.sourceEntryCount})\n`,
    `    ('scac-mutation-registry.v21','${HISTORICAL_REGISTRY_SEALS.v21.digest}',${HISTORICAL_REGISTRY_SEALS.v21.entryCount},${HISTORICAL_REGISTRY_SEALS.v21.sourceEntryCount}),\n    ('${v22Seal.version}','${v22Seal.digest}',${v22Seal.entryCount},${v22Seal.sourceEntryCount})\n`,
    "R07 repo hygiene janitor historical seal tuple");
  sql = replaceExactlyOnce(sql,
    "    ops.scac_mutation_registry_v21_seal_available()) then",
    "    ops.scac_mutation_registry_v21_seal_available() and\n    ops.scac_mutation_registry_v22_seal_available()) then",
    "R07 repo hygiene janitor snapshot predecessor seal");
  sql = replaceExactlyOnce(sql,
    `or (r.registry_version='scac-mutation-registry.v23' and r.registry_digest='${v22Seal.digest}')`,
    `or (r.registry_version='scac-mutation-registry.v22' and r.registry_digest='${v22Seal.digest}')\n         or (r.registry_version='scac-mutation-registry.v23' and r.registry_digest='sha256:${v23Digest}')`,
    "R07 repo hygiene janitor epoch-chain digest cases");
  sql = replaceExactlyOnce(sql,
    `  (registry_version='scac-mutation-registry.v23' and registry_digest='${v22Seal.digest}')`,
    `  (registry_version='scac-mutation-registry.v22' and registry_digest='${v22Seal.digest}') or\n  (registry_version='scac-mutation-registry.v23' and registry_digest='sha256:${v23Digest}')`,
    "R07 repo hygiene janitor epoch constraint digest cases");
  sql = replaceExactlyOnce(sql,
    `'{registry_digest}',to_jsonb('${v22Seal.digest}'::text)`,
    `'{registry_digest}',to_jsonb('sha256:${v23Digest}'::text)`,
    "R07 repo hygiene janitor snapshot registry digest");
  sql = replaceExactlyOnce(sql,
    "ops.scac_mutation_registry_v20_seal_available(),ops.scac_mutation_catalog_v21_live_at_seal(),ops.scac_mutation_catalog_v21_current(),ops.scac_mutation_registry_v21_seal_available(),ops.scac_mutation_catalog_v23_current()",
    "ops.scac_mutation_registry_v20_seal_available(),ops.scac_mutation_catalog_v21_live_at_seal(),ops.scac_mutation_catalog_v21_current(),ops.scac_mutation_registry_v21_seal_available(),ops.scac_mutation_catalog_v22_live_at_seal(),ops.scac_mutation_catalog_v22_current(),ops.scac_mutation_registry_v22_seal_available(),ops.scac_mutation_catalog_v23_current()",
    "R07 repo hygiene janitor historical function revoke list");
  sql = replaceExactlyOnce(sql,
    // The anchor is the predecessor comment AS IT STANDS AFTER the global
    // version rename above, which has already bumped "registry v22" to v23.
    "DoctorCRE portfolio successor snapshot: current policy epochs bind mutation registry v23 while historical v2/v3/v4/v5/v6/v7/v8/v9/v10/v11/v12/v13/v14/v15/v16/v17/v18/v19/v20/v21 epochs remain immutable.",
    "R07 repo-hygiene janitor successor snapshot: current policy epochs bind mutation registry v23 while historical v2/v3/v4/v5/v6/v7/v8/v9/v10/v11/v12/v13/v14/v15/v16/v17/v18/v19/v20/v21/v22 epochs remain immutable.",
    "R07 repo hygiene janitor policy snapshot comment");
  sql = replaceExactlyOnce(sql,
    `(select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v23')<>${v22Seal.entryCount}`,
    `(select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v23')<>${entryCount}`,
    "R07 repo hygiene janitor v23 entry count guard");
  sql = replaceExactlyOnce(sql,
    `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v21')<>'${HISTORICAL_REGISTRY_SEALS.v21.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v21')<>${HISTORICAL_REGISTRY_SEALS.v21.entryCount} then raise exception 'sealed SCAC mutation registry v21 changed during successor creation'; end if;`,
    `if (select registry_digest from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v22')<>'${v22Seal.digest}'\n     or (select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v22')<>${v22Seal.entryCount} then raise exception 'sealed SCAC mutation registry v22 changed during successor creation'; end if;`,
    "R07 repo hygiene janitor predecessor seal guard");
  sql = replaceExactlyOnce(sql,
    "ops.scac_policy_epoch_snapshot(),ops.scac_policy_epoch_snapshot_v6(),ops.scac_policy_epoch_snapshot_v7(),ops.scac_policy_epoch_snapshot_v8(),ops.scac_policy_epoch_snapshot_v9(),ops.scac_policy_epoch_snapshot_v10(),ops.scac_policy_epoch_snapshot_v11(),ops.scac_policy_epoch_snapshot_v12(),ops.scac_policy_epoch_snapshot_v13(),ops.scac_policy_epoch_snapshot_v14(),ops.scac_policy_epoch_snapshot_v15(),ops.scac_policy_epoch_snapshot_v16(),ops.scac_policy_epoch_snapshot_v17(),ops.scac_policy_epoch_snapshot_v18(),ops.scac_policy_epoch_snapshot_v19(),ops.scac_policy_epoch_snapshot_v20(),ops.scac_policy_epoch_snapshot_v21(),",
    "ops.scac_policy_epoch_snapshot(),ops.scac_policy_epoch_snapshot_v6(),ops.scac_policy_epoch_snapshot_v7(),ops.scac_policy_epoch_snapshot_v8(),ops.scac_policy_epoch_snapshot_v9(),ops.scac_policy_epoch_snapshot_v10(),ops.scac_policy_epoch_snapshot_v11(),ops.scac_policy_epoch_snapshot_v12(),ops.scac_policy_epoch_snapshot_v13(),ops.scac_policy_epoch_snapshot_v14(),ops.scac_policy_epoch_snapshot_v15(),ops.scac_policy_epoch_snapshot_v16(),ops.scac_policy_epoch_snapshot_v17(),ops.scac_policy_epoch_snapshot_v18(),ops.scac_policy_epoch_snapshot_v19(),ops.scac_policy_epoch_snapshot_v20(),ops.scac_policy_epoch_snapshot_v21(),ops.scac_policy_epoch_snapshot_v22(),",
    "R07 repo hygiene janitor historical policy snapshot revoke list");

  const seedStartMarker = "with seed as (select value as contract from jsonb_array_elements(";
  const seedEndMarker = "::jsonb))\ninsert into ops.scac_mutation_registry_entry";
  const seedStart = sql.indexOf(seedStartMarker);
  const secondSeedStart = sql.indexOf(seedStartMarker, seedStart + seedStartMarker.length);
  const seedEnd = sql.indexOf(seedEndMarker, seedStart + seedStartMarker.length);
  const secondSeedEnd = sql.indexOf(seedEndMarker, seedEnd + seedEndMarker.length);
  if (seedStart < 0 || secondSeedStart >= 0 || seedEnd < 0 || secondSeedEnd >= 0)
    throw new Error("sealed SCAC v22 migration has no exact source-seed boundary");
  const seed = JSON.stringify(rows.map(row => ({ ...row, entry_digest: `sha256:${sha256(row)}` })));
  sql = `${sql.slice(0, seedStart + seedStartMarker.length)}${sqlLiteral(seed)}${sql.slice(seedEnd)}`;

  const preflightCurrent = renderV23Current(predecessorDbCatalogBaseline);
  const preflightBegin = preflightCurrent.indexOf("begin\n");
  const preflightEnd = preflightCurrent.lastIndexOf("end $fn$;");
  if (preflightBegin < 0 || preflightEnd <= preflightBegin)
    throw new Error("generated v23 catalog predicate has no exact preflight body boundary");
  let preflightBody = preflightCurrent.slice(preflightBegin + "begin\n".length, preflightEnd);
  preflightBody = preflightBody.replaceAll(
    "then return false; end if;",
    "then raise exception 'R07 repo hygiene janitor pre-v23 catalog receipt drifted'; end if;");
  preflightBody = replaceExactlyOnce(preflightBody,
    `return observed_count=${predecessorDbCatalogBaseline.role_authority.count} and observed_digest='${predecessorDbCatalogBaseline.role_authority.digest}';`,
    `if observed_count<>${predecessorDbCatalogBaseline.role_authority.count} or observed_digest<>'${predecessorDbCatalogBaseline.role_authority.digest}' then raise exception 'R07 repo hygiene janitor pre-v23 role-authority receipt drifted'; end if;`,
    "R07 repo hygiene janitor pre-v23 role receipt");
  const predecessorHash = sha256(v22Migration);
  const predecessorPreflight =
`-- Exact disposable-Postgres post-0496 receipt. Refuse before any v23 function
-- exists; this registry-only successor changes no domain DDL or business rows.
do $r07_repo_hygiene_janitor_preflight$
declare observed_count integer; observed_digest text; grant_snapshot jsonb;
begin
  if (select count(*) from public.schema_migrations where filename='0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql')<>1
     or not exists(select 1 from public.schema_migrations where filename='0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql'
       and sha256='${predecessorHash}') then
    raise exception 'R07 repo hygiene janitor pre-v23 migration ledger receipt drifted';
  end if;
  grant_snapshot:=ops.scac_runtime_dml_grant_snapshot();
  if (grant_snapshot->>'entry_count')::integer<>${predecessorDbCatalogBaseline.runtime_dml_grants.count}
     or grant_snapshot->>'grant_digest'<>'${predecessorDbCatalogBaseline.runtime_dml_grants.digest}' then
    raise exception 'R07 repo hygiene janitor pre-v23 runtime grant receipt drifted';
  end if;
${preflightBody}end $r07_repo_hygiene_janitor_preflight$;

`;
  return `${predecessorPreflight}${sql}`.replace(/\n+$/, "\n");
}



export function renderGeneratedFrontier() {
  // Refuse before the expensive v2-v20 predecessor cascade: this frontier ends
  // in v21 artifacts, and every input to the guard is a fixed module constant.
  // The v21 root re-asserts the whole v20 chain, so an unbound v19 limb still
  // refuses here before any predecessor renderer runs.
  assertDoctorcrePortfolioV22TrustRoot();
  const v2Rows = frozenInventory(REGISTRY_V2_VERSION);
  const artifacts = {};
  artifacts["migrations/0454_siep11_mutation_registry.sql"] = renderMigration(v2Rows);
  if (sha256(artifacts["migrations/0454_siep11_mutation_registry.sql"]) !==
      HISTORICAL_REGISTRY_ARTIFACT_SHA256["migrations/0454_siep11_mutation_registry.sql"])
    throw new Error("generated historical SCAC v1 migration SHA drifted");
  artifacts["mcp-server/src/scac-mutation-registry.v2.generated.js"] =
    renderRuntimeProjection(v2Rows, {
      version: REGISTRY_V2_VERSION, dbCatalogBaseline: SIEP12_DB_CATALOG_BASELINE,
    });
  artifacts["migrations/0455_siep12_policy_epoch.sql"] = renderPolicyEpochMigration(
    renderSuccessorRegistrySql(v2Rows), {
      v1Seal: HISTORICAL_REGISTRY_SEALS.v1,
      dbCatalogBaseline: SIEP12_DB_CATALOG_BASELINE,
    });

  for (const path of Object.keys(STATIC_FRONTIER_MIGRATION_ARTIFACT_SHA256))
    artifacts[path] = staticFrontierMigration(path);
  for (const [path, expectedSha] of Object.entries(DIRECT_REGISTRY_MIGRATION_ARTIFACT_SHA256)) {
    const fixture = directMigrationPreimage(path);
    const rendered = renderDirectRegistryRedefinition(fixture.preimage, {
      ownerExclusion: fixture.owner_exclusion,
    });
    if (sha256(rendered) !== expectedSha)
      throw new Error(`${path} direct migration artifact SHA drifted`);
    artifacts[path] = rendered;
  }

  const v3Rows = frozenInventory(REGISTRY_V3_VERSION);
  artifacts["mcp-server/src/scac-mutation-registry.v3.generated.js"] =
    renderRuntimeProjection(v3Rows, {
      version: REGISTRY_V3_VERSION, dbCatalogBaseline: SIEP13_DB_CATALOG_BASELINE,
    });
  artifacts["migrations/0457_siep13_forward_mutation_registry.sql"] =
    renderSIEP13RegistrySql(v3Rows, SIEP13_DB_CATALOG_BASELINE);

  const v4Rows = frozenInventory(REGISTRY_V4_VERSION);
  artifacts["mcp-server/src/scac-mutation-registry.v4.generated.js"] =
    renderRuntimeProjection(v4Rows, {
      version: REGISTRY_V4_VERSION, dbCatalogBaseline: SIEP14_DB_CATALOG_BASELINE,
    });
  artifacts["migrations/0459_siep14_forward_mutation_registry.sql"] =
    renderSIEP14RegistrySql(v4Rows, SIEP14_DB_CATALOG_BASELINE);

  const v5Rows = frozenInventory(REGISTRY_V5_VERSION);
  artifacts["mcp-server/src/scac-mutation-registry.v5.generated.js"] =
    renderRuntimeProjection(v5Rows, {
      version: REGISTRY_V5_VERSION, dbCatalogBaseline: SIEP15_DB_CATALOG_BASELINE,
    });
  artifacts["migrations/0461_siep15_forward_mutation_registry.sql"] =
    renderSIEP15RegistrySql(v5Rows, SIEP15_DB_CATALOG_BASELINE,
      artifacts["migrations/0459_siep14_forward_mutation_registry.sql"]);

  const v6Rows = frozenInventory(REGISTRY_V6_VERSION);
  artifacts["mcp-server/src/scac-mutation-registry.v6.generated.js"] =
    renderRuntimeProjection(v6Rows, {
      version: REGISTRY_V6_VERSION, dbCatalogBaseline: SIEP16_DB_CATALOG_BASELINE,
    });
  artifacts["migrations/0462_siep16_forward_mutation_registry.sql"] =
    renderSIEP16RegistrySql(v6Rows, SIEP16_DB_CATALOG_BASELINE,
      artifacts["migrations/0461_siep15_forward_mutation_registry.sql"]);

  const v7Rows = frozenInventory(REGISTRY_V7_VERSION);
  artifacts["mcp-server/src/scac-mutation-registry.v7.generated.js"] =
    renderRuntimeProjection(v7Rows, {
      version: REGISTRY_V7_VERSION, dbCatalogBaseline: SIEP16_INTEGRATED_DB_CATALOG_BASELINE,
    });
  artifacts["migrations/0464_siep16_integrated_mutation_registry.sql"] =
    renderSIEP16IntegratedRegistrySql(v7Rows, SIEP16_INTEGRATED_DB_CATALOG_BASELINE,
      artifacts["migrations/0462_siep16_forward_mutation_registry.sql"]);

  const v8Rows = frozenInventory(REGISTRY_V8_VERSION);
  artifacts["mcp-server/src/scac-mutation-registry.v8.generated.js"] =
    renderRuntimeProjection(v8Rows, {
      version: REGISTRY_V8_VERSION, dbCatalogBaseline: SIEP17_FORWARD_DB_CATALOG_BASELINE,
    });
  artifacts["migrations/0466_siep17_forward_mutation_registry.sql"] =
    renderSIEP17ForwardRegistrySql(v8Rows, SIEP17_FORWARD_DB_CATALOG_BASELINE, {
      migration: artifacts["migrations/0464_siep16_integrated_mutation_registry.sql"],
      runtime: artifacts["mcp-server/src/scac-mutation-registry.v7.generated.js"],
    });

  const v9Rows = frozenInventory(REGISTRY_V9_VERSION);
  artifacts["mcp-server/src/scac-mutation-registry.v9.generated.js"] =
    renderRuntimeProjection(v9Rows, {
      version: REGISTRY_V9_VERSION, dbCatalogBaseline: SIEP18_FORWARD_DB_CATALOG_BASELINE,
    });
  artifacts["migrations/0468_siep18_forward_mutation_registry.sql"] =
    renderSIEP18ForwardRegistrySql(v9Rows, SIEP18_FORWARD_DB_CATALOG_BASELINE, {
      migration: artifacts["migrations/0466_siep17_forward_mutation_registry.sql"],
      runtime: artifacts["mcp-server/src/scac-mutation-registry.v8.generated.js"],
      monitor: artifacts["migrations/0467_siep18_atomic_db_monitor_grants.sql"],
    });

  const v10Rows = frozenInventory(REGISTRY_V10_VERSION);
  artifacts["mcp-server/src/scac-mutation-registry.v10.generated.js"] =
    renderRuntimeProjection(v10Rows, {
      version: REGISTRY_V10_VERSION, dbCatalogBaseline: SOURCE_MERGE_FORWARD_DB_CATALOG_BASELINE,
    });
  artifacts["migrations/0471_source_merge_catalog_registry_successor.sql"] =
    renderSourceMergeForwardRegistrySql(v10Rows, SOURCE_MERGE_FORWARD_DB_CATALOG_BASELINE, {
      migration: artifacts["migrations/0468_siep18_forward_mutation_registry.sql"],
      runtime: artifacts["mcp-server/src/scac-mutation-registry.v9.generated.js"],
    });

  const v11Rows = frozenInventory(REGISTRY_V11_VERSION);
  artifacts["mcp-server/src/scac-mutation-registry.v11.generated.js"] =
    renderRuntimeProjection(v11Rows, {
      version: REGISTRY_V11_VERSION,
      dbCatalogBaseline: CODEX_CONTINUITY_FORWARD_DB_CATALOG_BASELINE,
    });
  artifacts["migrations/0481_codex_continuity_registry_activation.sql"] =
    renderCodexContinuityForwardRegistrySql(v11Rows,
      CODEX_CONTINUITY_FORWARD_DB_CATALOG_BASELINE, {
        migration: artifacts["migrations/0471_source_merge_catalog_registry_successor.sql"],
        runtime: artifacts["mcp-server/src/scac-mutation-registry.v10.generated.js"],
      });

  const v12Rows = frozenInventory(REGISTRY_V12_VERSION);
  artifacts["mcp-server/src/scac-mutation-registry.v12.generated.js"] =
    renderRuntimeProjection(v12Rows, {
      version: REGISTRY_V12_VERSION,
      dbCatalogBaseline: CLAUDE_CONTINUITY_FORWARD_DB_CATALOG_BASELINE,
    });
  artifacts["migrations/0486_claude_continuity_registry_activation.sql"] =
    renderClaudeContinuityForwardRegistrySql(v12Rows,
      CLAUDE_CONTINUITY_FORWARD_DB_CATALOG_BASELINE, {
        migration: artifacts["migrations/0481_codex_continuity_registry_activation.sql"],
        runtime: artifacts["mcp-server/src/scac-mutation-registry.v11.generated.js"],
      });

  const v13Rows = frozenInventory(REGISTRY_V13_VERSION);
  artifacts["mcp-server/src/scac-mutation-registry.v13.generated.js"] =
    renderRuntimeProjection(v13Rows, {
      version: REGISTRY_V13_VERSION,
      dbCatalogBaseline: CLAUDE_STARTUP_FORWARD_DB_CATALOG_BASELINE,
    });
  artifacts["migrations/0487_claude_startup_registry_activation.sql"] =
    renderClaudeStartupForwardRegistrySql(v13Rows,
      CLAUDE_STARTUP_FORWARD_DB_CATALOG_BASELINE, {
        migration: artifacts["migrations/0486_claude_continuity_registry_activation.sql"],
        runtime: artifacts["mcp-server/src/scac-mutation-registry.v12.generated.js"],
      });

  const v14Rows = frozenInventory(REGISTRY_V14_VERSION);
  artifacts["mcp-server/src/scac-mutation-registry.v14.generated.js"] =
    renderRuntimeProjection(v14Rows, {
      version: REGISTRY_V14_VERSION,
      dbCatalogBaseline: CLAUDE_ACTOR_HYDRATION_FORWARD_DB_CATALOG_BASELINE,
    });
  artifacts["migrations/0488_claude_actor_hydration_registry_activation.sql"] =
    renderClaudeActorHydrationForwardRegistrySql(v14Rows,
      CLAUDE_ACTOR_HYDRATION_FORWARD_DB_CATALOG_BASELINE, {
        migration: artifacts["migrations/0487_claude_startup_registry_activation.sql"],
        runtime: artifacts["mcp-server/src/scac-mutation-registry.v13.generated.js"],
      });

  const v15Rows = frozenInventory(REGISTRY_V15_VERSION);
  artifacts["mcp-server/src/scac-mutation-registry.v15.generated.js"] =
    renderRuntimeProjection(v15Rows, {
      version: REGISTRY_V15_VERSION,
      dbCatalogBaseline: CLAUDE_CONFIG_PRESERVATION_FORWARD_DB_CATALOG_BASELINE,
    });
  artifacts["migrations/0489_claude_config_preservation_registry_activation.sql"] =
    renderClaudeConfigPreservationForwardRegistrySql(v15Rows,
      CLAUDE_CONFIG_PRESERVATION_FORWARD_DB_CATALOG_BASELINE, {
        migration: artifacts["migrations/0488_claude_actor_hydration_registry_activation.sql"],
        runtime: artifacts["mcp-server/src/scac-mutation-registry.v14.generated.js"],
      });

  const v16Rows = frozenInventory(REGISTRY_V16_VERSION);
  artifacts["mcp-server/src/scac-mutation-registry.v16.generated.js"] =
    renderRuntimeProjection(v16Rows, {
      version: REGISTRY_V16_VERSION,
      dbCatalogBaseline: CODEX_COMPACTION_CHECKPOINT_FORWARD_DB_CATALOG_BASELINE,
    });
  artifacts["migrations/0490_codex_compaction_checkpoint_registry_activation.sql"] =
    renderCodexCompactionCheckpointForwardRegistrySql(v16Rows,
      CODEX_COMPACTION_CHECKPOINT_FORWARD_DB_CATALOG_BASELINE, {
        migration: artifacts["migrations/0489_claude_config_preservation_registry_activation.sql"],
        runtime: artifacts["mcp-server/src/scac-mutation-registry.v15.generated.js"],
      });

  const v17Rows = frozenInventory(REGISTRY_V17_VERSION);
  artifacts["mcp-server/src/scac-mutation-registry.v17.generated.js"] =
    renderRuntimeProjection(v17Rows, {
      version: REGISTRY_V17_VERSION,
      dbCatalogBaseline: BACKUP_GUARD_STATUS_FORWARD_DB_CATALOG_BASELINE,
    });
  artifacts["migrations/0491_backup_guard_status_registry_activation.sql"] =
    renderBackupGuardStatusForwardRegistrySql(v17Rows,
      BACKUP_GUARD_STATUS_FORWARD_DB_CATALOG_BASELINE, {
        migration: artifacts["migrations/0490_codex_compaction_checkpoint_registry_activation.sql"],
        runtime: artifacts["mcp-server/src/scac-mutation-registry.v16.generated.js"],
      });

  const v18Rows = frozenInventory(REGISTRY_V18_VERSION);
  artifacts["mcp-server/src/scac-mutation-registry.v18.generated.js"] =
    renderRuntimeProjection(v18Rows, {
      version: REGISTRY_V18_VERSION,
      dbCatalogBaseline: SOURCED_SHAPE_FORWARD_CORRECTION_FORWARD_DB_CATALOG_BASELINE,
    });
  artifacts["migrations/0492_sourced_shape_forward_correction_and_scac_successor.sql"] =
    renderSourcedShapeForwardCorrectionRegistrySql(v18Rows,
      SOURCED_SHAPE_FORWARD_CORRECTION_FORWARD_DB_CATALOG_BASELINE, {
        migration: artifacts["migrations/0491_backup_guard_status_registry_activation.sql"],
        runtime: artifacts["mcp-server/src/scac-mutation-registry.v17.generated.js"],
        witnesses: {
          "migrations/0470_source_merge_authority_projection.sql":
            artifacts["migrations/0470_source_merge_authority_projection.sql"],
        },
      });

  const v19Rows = frozenInventory(REGISTRY_V19_VERSION);
  artifacts["mcp-server/src/scac-mutation-registry.v19.generated.js"] =
    renderRuntimeProjection(v19Rows, {
      version: REGISTRY_V19_VERSION,
      dbCatalogBaseline: INCIDENT_WORK_REQUEST_LINK_FORWARD_DB_CATALOG_BASELINE,
    });
  artifacts["migrations/0493_incident_work_request_link_scac_successor.sql"] =
    renderIncidentWorkRequestLinkRegistrySql(v19Rows,
      INCIDENT_WORK_REQUEST_LINK_FORWARD_DB_CATALOG_BASELINE, {
        migration: artifacts["migrations/0492_sourced_shape_forward_correction_and_scac_successor.sql"],
        runtime: artifacts["mcp-server/src/scac-mutation-registry.v18.generated.js"],
      });

  const v20Rows = frozenInventory(REGISTRY_V20_VERSION);
  artifacts["mcp-server/src/scac-mutation-registry.v20.generated.js"] =
    renderRuntimeProjection(v20Rows, {
      version: REGISTRY_V20_VERSION,
      dbCatalogBaseline: CONTINUITY_ARCHIVE_FORWARD_DB_CATALOG_BASELINE,
    });
  artifacts["migrations/0494_codex_continuity_archive_registry.sql"] =
    renderContinuityArchiveForwardRegistrySql(v20Rows,
      CONTINUITY_ARCHIVE_FORWARD_DB_CATALOG_BASELINE, {
        migration: artifacts["migrations/0493_incident_work_request_link_scac_successor.sql"],
        runtime: artifacts["mcp-server/src/scac-mutation-registry.v19.generated.js"],
      });

  const v21Rows = frozenInventory(REGISTRY_V21_VERSION);
  artifacts["mcp-server/src/scac-mutation-registry.v21.generated.js"] =
    renderRuntimeProjection(v21Rows, {
      version: REGISTRY_V21_VERSION,
      dbCatalogBaseline: R06_HOOKS_CORRECTNESS_FORWARD_DB_CATALOG_BASELINE,
    });
  artifacts["migrations/0495_r06_hooks_correctness_scac_successor.sql"] =
    renderR06HooksCorrectnessForwardRegistrySql(v21Rows,
      R06_HOOKS_CORRECTNESS_FORWARD_DB_CATALOG_BASELINE, {
        migration: artifacts["migrations/0494_codex_continuity_archive_registry.sql"],
        runtime: artifacts["mcp-server/src/scac-mutation-registry.v20.generated.js"],
      });

  const v22Rows = frozenInventory(REGISTRY_V22_VERSION);
  artifacts["mcp-server/src/scac-mutation-registry.v22.generated.js"] =
    renderRuntimeProjection(v22Rows, {
      version: REGISTRY_V22_VERSION,
      dbCatalogBaseline: DOCTORCRE_PORTFOLIO_FORWARD_DB_CATALOG_BASELINE,
    });
  artifacts["migrations/0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql"] =
    renderDoctorcrePortfolioForwardRegistrySql(v22Rows,
      DOCTORCRE_PORTFOLIO_FORWARD_DB_CATALOG_BASELINE, {
        migration: artifacts["migrations/0495_r06_hooks_correctness_scac_successor.sql"],
        runtime: artifacts["mcp-server/src/scac-mutation-registry.v21.generated.js"],
      });

  const v23Rows = frozenInventory(REGISTRY_V23_VERSION);
  artifacts["mcp-server/src/scac-mutation-registry.v23.generated.js"] =
    renderRuntimeProjection(v23Rows, {
      version: REGISTRY_V23_VERSION,
      dbCatalogBaseline: R07_REPO_HYGIENE_JANITOR_FORWARD_DB_CATALOG_BASELINE,
    });
  artifacts["migrations/0497_r07_repo_hygiene_janitor_and_scac_successor.sql"] =
    renderR07RepoHygieneJanitorForwardRegistrySql(v23Rows,
      R07_REPO_HYGIENE_JANITOR_FORWARD_DB_CATALOG_BASELINE, {
        migration: artifacts["migrations/0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql"],
        runtime: artifacts["mcp-server/src/scac-mutation-registry.v22.generated.js"],
      });

  const migrationCount = Object.keys(artifacts).filter(path => path.startsWith("migrations/")).length;
  const runtimeCount = Object.keys(artifacts).filter(path => path.startsWith("mcp-server/src/")).length;
  if (migrationCount !== 31 || runtimeCount !== 22 || Object.keys(artifacts).length !== 53)
    throw new Error(`generated frontier is incomplete: ${migrationCount} migrations, ${runtimeCount} runtimes`);
  return Object.freeze(artifacts);
}

export function assertGeneratedFrontierMatchesCommitted() {
  const artifacts = renderGeneratedFrontier();
  for (const [path, rendered] of Object.entries(artifacts)) {
    const committed = readFileSync(resolve(REPO_ROOT, path), "utf8");
    if (rendered !== committed) throw new Error(`${path} is not byte-reproducible`);
  }
  return Object.keys(artifacts);
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
  const directMigrationModes = {
    "--write-direct-migration-0460": "migrations/0460_siep15_device_enrollment.sql",
    "--write-direct-migration-0465": "migrations/0465_siep17_token_challenge_authority.sql",
    "--write-direct-migration-0467": "migrations/0467_siep18_atomic_db_monitor_grants.sql",
    "--write-direct-migration-0470": "migrations/0470_source_merge_authority_projection.sql",
  };
  if (process.argv[2] === "--bless-full-entry-set-seals-from-local-db") {
    const dsn = process.env.CARR_LOCAL_PG_DSN || "";
    if (!dsn) throw new Error("CARR_LOCAL_PG_DSN is required for local seal blessing");
    const seals = fullEntrySetSealsFromLocalDatabase(dsn);
    await writeFile(FULL_ENTRY_SET_SEALS_PATH, `${JSON.stringify(seals, null, 2)}\n`);
    process.stdout.write(`${fileURLToPath(FULL_ENTRY_SET_SEALS_PATH)} (10 recomputed seals)\n`);
  } else if (process.argv[2] === "--write-generated-frontier") {
    if (!process.argv[3]) throw new Error("--write-generated-frontier requires an output directory");
    const outputRoot = resolve(process.argv[3]);
    const artifacts = renderGeneratedFrontier();
    for (const [path, source] of Object.entries(artifacts)) {
      const target = resolve(outputRoot, path);
      await mkdir(dirname(target), { recursive: true });
      await writeFile(target, source);
    }
    process.stdout.write(`${outputRoot} (${Object.keys(artifacts).length} artifacts)\n`);
  } else if (directMigrationModes[process.argv[2]]) {
    const sourcePath = directMigrationModes[process.argv[2]];
    const target = resolve(process.argv[3] || sourcePath);
    const fixture = directMigrationPreimage(sourcePath);
    await writeFile(target, renderDirectRegistryRedefinition(fixture.preimage, {
      ownerExclusion: fixture.owner_exclusion,
    }));
    process.stdout.write(`${target}\n`);
  } else if (rebasedRuntimeModes[process.argv[2]]) {
    const [version, dbCatalogBaseline, defaultTarget] = rebasedRuntimeModes[process.argv[2]];
    const target = resolve(process.argv[3] || defaultTarget);
    const rows = version === REGISTRY_VERSION ? fullInventory(await loadDefaultTools()) : frozenInventory(version);
    await writeFile(target, renderRuntimeProjection(rows, { version, dbCatalogBaseline }));
    process.stdout.write(`${target}\n`);
  } else if (rebasedMigrationModes[process.argv[2]]) {
    const [render, defaultTarget] = rebasedMigrationModes[process.argv[2]];
    const target = resolve(process.argv[3] || defaultTarget);
    const version = process.argv[2].match(/v(\d+)$/)?.[1];
    const rows = version === "1" ? fullInventory(await loadDefaultTools()) : frozenInventory(`scac-mutation-registry.v${version}`);
    await writeFile(target, render(rows));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-rebased-migration-v2") {
    const target = resolve(process.argv[3] || "migrations/0455_siep12_policy_epoch.sql");
    const rows = frozenInventory(REGISTRY_V2_VERSION);
    // WR-000048: v1Seal must be the FROZEN HISTORICAL_REGISTRY_SEALS.v1, matching
    // migration 0454's actual committed bytes -- see the matching comment in
    // renderSuccessorRegistrySql above.
    const v1Seal = HISTORICAL_REGISTRY_SEALS.v1;
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
    const rows = frozenInventory(REGISTRY_V7_VERSION);
    await writeFile(target, renderRuntimeProjection(rows, {
      version: REGISTRY_V7_VERSION,
      dbCatalogBaseline: SIEP16_INTEGRATED_DB_CATALOG_BASELINE,
    }));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-siep16-integrated-registry-migration") {
    const target = resolve(process.argv[3] || "migrations/0464_siep16_integrated_mutation_registry.sql");
    const rows = frozenInventory(REGISTRY_V7_VERSION);
    await writeFile(target, renderSIEP16IntegratedRegistrySql(rows));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-runtime-v9") {
    const target = resolve(process.argv[3] || "mcp-server/src/scac-mutation-registry.v9.generated.js");
    const rows = frozenInventory(REGISTRY_V9_VERSION);
    await writeFile(target, renderRuntimeProjection(rows, {
      version: REGISTRY_V9_VERSION,
      dbCatalogBaseline: SIEP18_FORWARD_DB_CATALOG_BASELINE,
    }));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-siep18-forward-registry-migration") {
    const target = resolve(process.argv[3] || "migrations/0468_siep18_forward_mutation_registry.sql");
    const rows = frozenInventory(REGISTRY_V9_VERSION);
    await writeFile(target, renderSIEP18ForwardRegistrySql(rows));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-runtime-v10") {
    const target = resolve(process.argv[3] || "mcp-server/src/scac-mutation-registry.v10.generated.js");
    const rows = frozenInventory(REGISTRY_V10_VERSION);
    await writeFile(target, renderRuntimeProjection(rows, {
      version: REGISTRY_V10_VERSION,
      dbCatalogBaseline: SOURCE_MERGE_FORWARD_DB_CATALOG_BASELINE,
    }));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-source-merge-forward-registry-migration") {
    const target = resolve(process.argv[3] || "migrations/0471_source_merge_catalog_registry_successor.sql");
    const rows = frozenInventory(REGISTRY_V10_VERSION);
    await writeFile(target, renderSourceMergeForwardRegistrySql(rows));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-runtime-v11") {
    const target = resolve(process.argv[3] || "mcp-server/src/scac-mutation-registry.v11.generated.js");
    const rows = frozenInventory(REGISTRY_V11_VERSION);
    await writeFile(target, renderRuntimeProjection(rows, {
      version: REGISTRY_V11_VERSION,
      dbCatalogBaseline: CODEX_CONTINUITY_FORWARD_DB_CATALOG_BASELINE,
    }));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-codex-continuity-registry-migration") {
    const target = resolve(process.argv[3] || "migrations/0481_codex_continuity_registry_activation.sql");
    const rows = frozenInventory(REGISTRY_V11_VERSION);
    await writeFile(target, renderCodexContinuityForwardRegistrySql(rows));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-runtime-v12") {
    const target = resolve(process.argv[3] || "mcp-server/src/scac-mutation-registry.v12.generated.js");
    const rows = frozenInventory(REGISTRY_V12_VERSION);
    await writeFile(target, renderRuntimeProjection(rows, {
      version: REGISTRY_V12_VERSION,
      dbCatalogBaseline: CLAUDE_CONTINUITY_FORWARD_DB_CATALOG_BASELINE,
    }));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-claude-continuity-registry-migration") {
    const target = resolve(process.argv[3] || "migrations/0486_claude_continuity_registry_activation.sql");
    const rows = frozenInventory(REGISTRY_V12_VERSION);
    await writeFile(target, renderClaudeContinuityForwardRegistrySql(rows));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-runtime-v13") {
    const target = resolve(process.argv[3] || "mcp-server/src/scac-mutation-registry.v13.generated.js");
    const rows = frozenInventory(REGISTRY_V13_VERSION);
    await writeFile(target, renderRuntimeProjection(rows, {
      version: REGISTRY_V13_VERSION,
      dbCatalogBaseline: CLAUDE_STARTUP_FORWARD_DB_CATALOG_BASELINE,
    }));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-claude-startup-registry-migration") {
    const target = resolve(process.argv[3] || "migrations/0487_claude_startup_registry_activation.sql");
    const rows = frozenInventory(REGISTRY_V13_VERSION);
    await writeFile(target, renderClaudeStartupForwardRegistrySql(rows));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-runtime-v14") {
    const target = resolve(process.argv[3] || "mcp-server/src/scac-mutation-registry.v14.generated.js");
    const rows = frozenInventory(REGISTRY_V14_VERSION);
    await writeFile(target, renderRuntimeProjection(rows, {
      version: REGISTRY_V14_VERSION,
      dbCatalogBaseline: CLAUDE_ACTOR_HYDRATION_FORWARD_DB_CATALOG_BASELINE,
    }));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-claude-actor-hydration-registry-migration") {
    const target = resolve(process.argv[3] || "migrations/0488_claude_actor_hydration_registry_activation.sql");
    const rows = frozenInventory(REGISTRY_V14_VERSION);
    await writeFile(target, renderClaudeActorHydrationForwardRegistrySql(rows));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-runtime-v15") {
    const target = resolve(process.argv[3] || "mcp-server/src/scac-mutation-registry.v15.generated.js");
    const rows = frozenInventory(REGISTRY_V15_VERSION);
    await writeFile(target, renderRuntimeProjection(rows, {
      version: REGISTRY_V15_VERSION,
      dbCatalogBaseline: CLAUDE_CONFIG_PRESERVATION_FORWARD_DB_CATALOG_BASELINE,
    }));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-claude-config-preservation-registry-migration") {
    const target = resolve(process.argv[3] || "migrations/0489_claude_config_preservation_registry_activation.sql");
    const rows = frozenInventory(REGISTRY_V15_VERSION);
    await writeFile(target, renderClaudeConfigPreservationForwardRegistrySql(rows));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-runtime-v16") {
    const target = resolve(process.argv[3] || "mcp-server/src/scac-mutation-registry.v16.generated.js");
    const rows = frozenInventory(REGISTRY_V16_VERSION);
    await writeFile(target, renderRuntimeProjection(rows, {
      version: REGISTRY_V16_VERSION,
      dbCatalogBaseline: CODEX_COMPACTION_CHECKPOINT_FORWARD_DB_CATALOG_BASELINE,
    }));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-codex-compaction-checkpoint-registry-migration") {
    const target = resolve(process.argv[3] || "migrations/0490_codex_compaction_checkpoint_registry_activation.sql");
    const rows = frozenInventory(REGISTRY_V16_VERSION);
    await writeFile(target, renderCodexCompactionCheckpointForwardRegistrySql(rows));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-runtime-v17") {
    const target = resolve(process.argv[3] || "mcp-server/src/scac-mutation-registry.v17.generated.js");
    const rows = frozenInventory(REGISTRY_V17_VERSION);
    await writeFile(target, renderRuntimeProjection(rows, {
      version: REGISTRY_V17_VERSION,
      dbCatalogBaseline: BACKUP_GUARD_STATUS_FORWARD_DB_CATALOG_BASELINE,
    }));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-backup-guard-status-registry-migration") {
    const target = resolve(process.argv[3] || "migrations/0491_backup_guard_status_registry_activation.sql");
    const rows = frozenInventory(REGISTRY_V17_VERSION);
    await writeFile(target, renderBackupGuardStatusForwardRegistrySql(rows));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-runtime-v18") {
    const target = resolve(process.argv[3] || "mcp-server/src/scac-mutation-registry.v18.generated.js");
    const rows = frozenInventory(REGISTRY_V18_VERSION);
    await writeFile(target, renderRuntimeProjection(rows, {
      version: REGISTRY_V18_VERSION,
      dbCatalogBaseline: SOURCED_SHAPE_FORWARD_CORRECTION_FORWARD_DB_CATALOG_BASELINE,
    }));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-sourced-shape-forward-correction-registry-migration") {
    const target = resolve(process.argv[3] || "migrations/0492_sourced_shape_forward_correction_and_scac_successor.sql");
    const rows = frozenInventory(REGISTRY_V18_VERSION);
    await writeFile(target, renderSourcedShapeForwardCorrectionRegistrySql(rows));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-runtime-v20") {
    assertContinuityArchiveV20TrustRoot();
    const target = resolve(process.argv[3] || "mcp-server/src/scac-mutation-registry.v20.generated.js");
    const rows = frozenInventory(REGISTRY_V20_VERSION);
    await writeFile(target, renderRuntimeProjection(rows, {
      version: REGISTRY_V20_VERSION,
      dbCatalogBaseline: CONTINUITY_ARCHIVE_FORWARD_DB_CATALOG_BASELINE,
    }));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-runtime-v19") {
    const target = resolve(process.argv[3] || "mcp-server/src/scac-mutation-registry.v19.generated.js");
    const rows = frozenInventory(REGISTRY_V19_VERSION);
    await writeFile(target, renderRuntimeProjection(rows, {
      version: REGISTRY_V19_VERSION,
      dbCatalogBaseline: INCIDENT_WORK_REQUEST_LINK_FORWARD_DB_CATALOG_BASELINE,
    }));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-incident-work-request-link-registry-migration") {
    const target = resolve(process.argv[3] || "migrations/0493_incident_work_request_link_scac_successor.sql");
    const rows = frozenInventory(REGISTRY_V19_VERSION);
    await writeFile(target, renderIncidentWorkRequestLinkRegistrySql(rows));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-continuity-archive-registry-migration") {
    const target = resolve(process.argv[3] || "migrations/0494_codex_continuity_archive_registry.sql");
    const rows = frozenInventory(REGISTRY_V20_VERSION);
    await writeFile(target, renderContinuityArchiveForwardRegistrySql(rows));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-runtime-v21") {
    assertR06HooksCorrectnessV21TrustRoot();
    const target = resolve(process.argv[3] || "mcp-server/src/scac-mutation-registry.v21.generated.js");
    const rows = frozenInventory(REGISTRY_V21_VERSION);
    await writeFile(target, renderRuntimeProjection(rows, {
      version: REGISTRY_V21_VERSION,
      dbCatalogBaseline: R06_HOOKS_CORRECTNESS_FORWARD_DB_CATALOG_BASELINE,
    }));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-r06-hooks-correctness-registry-migration") {
    const target = resolve(process.argv[3] || "migrations/0495_r06_hooks_correctness_scac_successor.sql");
    const rows = frozenInventory(REGISTRY_V21_VERSION);
    await writeFile(target, renderR06HooksCorrectnessForwardRegistrySql(rows));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-runtime-v22") {
    assertDoctorcrePortfolioV22TrustRoot();
    const target = resolve(process.argv[3] || "mcp-server/src/scac-mutation-registry.v22.generated.js");
    const rows = frozenInventory(REGISTRY_V22_VERSION);
    await writeFile(target, renderRuntimeProjection(rows, {
      version: REGISTRY_V22_VERSION,
      dbCatalogBaseline: DOCTORCRE_PORTFOLIO_FORWARD_DB_CATALOG_BASELINE,
    }));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-doctorcre-portfolio-registry-migration") {
    const target = resolve(process.argv[3] ||
      "migrations/0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql");
    const rows = frozenInventory(REGISTRY_V22_VERSION);
    await writeFile(target, renderDoctorcrePortfolioForwardRegistrySql(rows));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-runtime-v23") {
    assertR07RepoHygieneJanitorV23TrustRoot();
    const target = resolve(process.argv[3] || "mcp-server/src/scac-mutation-registry.v23.generated.js");
    const rows = frozenInventory(REGISTRY_V23_VERSION);
    await writeFile(target, renderRuntimeProjection(rows, {
      version: REGISTRY_V23_VERSION,
      dbCatalogBaseline: R07_REPO_HYGIENE_JANITOR_FORWARD_DB_CATALOG_BASELINE,
    }));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-r07-repo-hygiene-janitor-registry-migration") {
    const target = resolve(process.argv[3] ||
      "migrations/0497_r07_repo_hygiene_janitor_and_scac_successor.sql");
    const rows = frozenInventory(REGISTRY_V23_VERSION);
    await writeFile(target, renderR07RepoHygieneJanitorForwardRegistrySql(rows));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--check-source-inventory-frontier") {
    assertCurrentSourceInventoryMatchesFixture(await loadDefaultTools());
    process.stdout.write(`source inventory matches frozen ${REGISTRY_V23_VERSION} frontier fixture\n`);
  } else if (process.argv[2] === "--check-generated-frontier") {
    const paths = assertGeneratedFrontierMatchesCommitted();
    process.stdout.write(`generated frontier is byte-exact (${paths.length} artifacts)\n`);
  } else {
    const rows = fullInventory(await loadDefaultTools());
    process.stdout.write(`${JSON.stringify({ schema_version: REGISTRY_VERSION, digest: registryDigest(rows), rows }, null, 2)}\n`);
  }
}
