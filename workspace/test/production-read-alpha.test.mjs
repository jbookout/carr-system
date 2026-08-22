import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const readJson = async (path) => JSON.parse(await readFile(new URL(`../${path}`, import.meta.url), "utf8"));

test("first production-read alpha is one flagged Team Book routine, not a surface retirement", async () => {
  const [traceability, surfaces] = await Promise.all([
    readJson("contracts/phase0-traceability.v1.json"),
    readJson("contracts/surface-registry-migration-map.v1.json"),
  ]);
  const commandCenter = traceability.entries.find((entry) => entry.id === "CC-HOME-001");
  assert.ok(commandCenter.api_or_action.includes("GET /api/v1/workspace/command-center/deal-attention"));
  assert.ok(commandCenter.evidence.some((item) => item.includes("v_deal_room_board")));
  assert.ok(commandCenter.rollback.some((item) => item.includes("WORKSPACE_COMMAND_CENTER_READ_ENABLED")));

  assert.equal(surfaces.pilot_slices.length, 1);
  assert.deepEqual(surfaces.pilot_slices[0], {
    id: "PILOT-WS-CC-DEAL-ATTENTION-001",
    requirement_ids: ["CC-HOME-001", "FLOW-WS-01"],
    source_surface: "SURF-001",
    delivery_surface: "SURF-006",
    owner_surface: "SURF-001",
    legacy_dependency: "panhandle-team-deals.json",
    scope: "signed-in partner's active flagged Team Book count and exact filtered Deal Room destination",
    canonical_read: "v_deal_room_board",
    endpoint: "GET /api/v1/workspace/command-center/deal-attention",
    destination: "/deals?workspace=team&filter=flagged&owner=me",
    status: "implemented_behind_flag_not_cut_over",
    production_flag: "false",
    staging_flag: "true",
    observation_required: "two weeks after approved production enablement",
    retirement_claim: "none; panhandle-team-deals.json remains compatibility-only until parity and observation complete",
  });
  for (const id of ["SURF-001", "SURF-006"]) {
    const surface = surfaces.surfaces.find((item) => item.id === id);
    assert.equal(surface.retirement_date, null);
  }
});
