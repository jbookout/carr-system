import { CURRENT_REGISTRY_VERSION } from "../../ops/scac-mutation-inventory.mjs";
import test from "node:test";
import assert from "node:assert/strict";
import { readRegistryArtifact as readFileSync } from '../../ops/registry-history.mjs';
import { frozenInventory, renderConfirmMergeRegistrySql, renderRuntimeProjection,
  CONFIRM_MERGE_V102_DB_CATALOG_BASELINE } from "../../ops/scac-mutation-inventory.mjs";
import { registeredOperation, SCAC_MUTATION_REGISTRY_VERSION } from "../src/mutation-registry.js";

test("confirm-merge v102 changes only its source contract and preserves v101 history", () => {
  const before = frozenInventory("scac-mutation-registry.v101");
  const after = frozenInventory("scac-mutation-registry.v102");
  assert.equal(after.length, before.length);
  for (let i = 0; i < before.length; i++) {
    if (before[i].ingress_key !== "mcp-tool:confirm-merge") assert.deepEqual(after[i], before[i]);
    else {
      assert.equal(before[i].human_only, false);
      assert.equal(after[i].human_only, true);
      assert.deepEqual(after[i], { ...before[i], human_only: true,
        principal_mode: "server_verified_human" });
    }
  }
  assert.equal(SCAC_MUTATION_REGISTRY_VERSION, CURRENT_REGISTRY_VERSION);
  assert.equal(registeredOperation("confirm-merge").human_only, true);
  assert.equal(readFileSync(new URL("../src/scac-mutation-registry.v102.generated.js", import.meta.url), "utf8"),
    renderRuntimeProjection(after, { version: "scac-mutation-registry.v102",
      dbCatalogBaseline: CONFIRM_MERGE_V102_DB_CATALOG_BASELINE }));
  const sql = renderConfirmMergeRegistrySql(after);
  assert.equal(readFileSync(new URL("../../migrations/0768_confirm_merge_human_only_scac_successor.sql", import.meta.url), "utf8"), sql);
  assert.match(sql, /0767_doc_whats_new_scac_successor.sql/);
  assert.match(sql, /scac_mutation_registry_v101_seal_available\(\)/);
  assert.match(sql, /scac_mutation_registry_v102_seal_available\(\)/);
});
