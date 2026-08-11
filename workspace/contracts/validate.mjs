import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import { join } from "node:path";
import { compileSchema } from "./schema-validator.mjs";

const contractDir = new URL("./", import.meta.url);
const workspaceDir = new URL("../", contractDir);
const fixtureDir = new URL("fixtures/", workspaceDir);
const readJson = async (base, name) => JSON.parse(await readFile(new URL(name, base), "utf8"));
const contracts = {};
for (const name of (await readdir(contractDir)).filter(name => name.endsWith(".json"))) contracts[name] = await readJson(contractDir, name);
const fixtureNames = (await readdir(fixtureDir)).filter(name => name.endsWith(".json")).sort();
const fixtures = await Promise.all(fixtureNames.map(name => readJson(fixtureDir, name)));
const registry = contracts["cross-reference-registry.v1.json"];
const trace = contracts["phase0-traceability.v1.json"];
const threats = contracts["threat-model.v1.json"];
const events = contracts["notification-event-taxonomy.v1.json"];
const acceptance = contracts["phase0-acceptance.v1.json"];
const environment = contracts["environment-release-process.v1.json"];
const council = contracts["council-review-register.v1.json"];
const fixtureContract = contracts["prototype-fixture-contract.v1.json"];
const api = contracts["business-entity-api-contracts.v1.json"];
const stateContract = contracts["state-machines.v1.json"];
const surfaceMap = contracts["surface-registry-migration-map.v1.json"];
const exact = (actual, expected, label) => assert.deepEqual([...actual].sort(), [...expected].sort(), label);
const validateFixture = compileSchema(fixtureContract);
assert.equal(api.api_policy.prototype_fixture_schema, fixtureContract.$id, "prototype fixture/API schema linkage");
exact(api.api_policy.prototype_methods, ["GET", "HEAD", "OPTIONS"], "prototype method contract");
for (const schema of Object.values(api.$defs)) compileSchema(api, schema);
for (const [index, fixture] of fixtures.entries()) {
  const result = validateFixture(fixture);
  assert.equal(result.valid, true, `${fixtureNames[index]} schema errors:\n${result.errors.join("\n")}`);
}

const requirementIds = new Set(trace.entries.map(entry => entry.id));
const aliases = new Map(registry.requirement_aliases.map(alias => [alias.id, alias.canonical]));
for (const [id, canonical] of aliases) {
  assert(requirementIds.has(canonical), `requirement alias ${id} -> ${canonical}`);
  assert(!aliases.has(canonical), `requirement alias may not chain: ${id}`);
}
for (const fixture of fixtures) for (const id of fixture.requirement_ids) assert(requirementIds.has(id) || aliases.has(id), `fixture requirement ${id}`);
for (const flow of acceptance.primary_uncoached_journeys) for (const id of flow.requirements) assert(requirementIds.has(id) || aliases.has(id), `flow requirement ${id}`);

const testIds = new Set(registry.test_ids);
const futureGateIds = new Set(environment.runtime_verification_gates.map(item => item.test_id));
assert.equal(futureGateIds.size, environment.runtime_verification_gates.length, "unique future gate IDs");
for (const gate of environment.runtime_verification_gates) {
  assert(testIds.has(gate.test_id), `future gate ${gate.test_id} must be in the test catalog`);
  assert.equal(gate.status, "future_gate_not_implemented_in_phase0_static_prototype", `${gate.test_id} future status`);
  assert(gate.required_evidence.length >= 2, `${gate.test_id} future evidence`);
}
for (const entry of trace.entries) for (const id of entry.tests) assert(testIds.has(id), `trace test ${id}`);
for (const threat of threats.threats) for (const id of threat.tests) assert(testIds.has(id), `threat test ${id}`);
for (const event of events.events) for (const id of event.test_ids) assert(testIds.has(id), `event test ${id}`);
const eventIds = new Set(events.events.map(event => event.name));
for (const entry of trace.entries) for (const id of entry.event) assert(eventIds.has(id), `trace event ${id}`);
const councilIds = new Set([council.review_event.id, ...council.open_items.map(item => item.id)]);
for (const entry of trace.entries) for (const id of entry.open_decisions) assert(councilIds.has(id), `council ID ${id}`);
const settledIds = new Set(council.settled_inputs.map(item => item.id));
const councilLinkIds = new Set([...councilIds, ...settledIds]);
for (const adr of council.adr_candidates) for (const id of adr.linked) assert(councilLinkIds.has(id), `ADR ${adr.id} link ${id}`);
assert.equal(new Set(council.current_evidence_inputs.map(item => item.id)).size, council.current_evidence_inputs.length, "unique council evidence IDs");
const baselineIds = new Set(acceptance.baseline_measure_plan.map(item => item.id));
exact(contracts["surface-registry-migration-map.v1.json"].baseline_measure_ids, baselineIds, "surface baseline refs");
assert.equal(acceptance.baseline_measure_plan.length, 5, "five baseline measures");
for (const baseline of acceptance.baseline_measure_plan) {
  for (const field of ["measure", "baseline_method", "bound_product_action", "post_launch_comparison"]) assert(baseline[field], `${baseline.id}.${field}`);
  assert(!("value" in baseline), `${baseline.id} must not invent a value`);
}
assert.equal(contracts["domain-glossary.v1.json"].operating_objective.objectives.length, 8, "eight objectives");
const cutoverOwners = new Map(surfaceMap.cutover_owner_assignments.map(item => [item.id, item]));
assert.equal(cutoverOwners.size, 5, "five explicit surface cutover owner assignments");
for (const surface of surfaceMap.surfaces.filter(item => item.cutover_owner.startsWith("OPEN-SURFACE-OWNER-"))) assert.equal(cutoverOwners.get(surface.cutover_owner)?.surface_id, surface.id, `surface owner ${surface.cutover_owner}`);
assert(testIds.has("FRONTDOOR-USAGE-001"), "Front Door usage gate test ID");

const endpointFields = api.api_policy.required_endpoint_declarations.map(field => field === "idempotency" ? "idempotency_or_read_semantics" : field === "response_schema" ? "response_schema" : field);
for (const endpoint of api.prototype_read_routes) for (const field of endpointFields) assert(field in endpoint, `${endpoint.operation_id}.${field}`);
for (const endpoint of api.prototype_read_routes) assert(api.$defs[endpoint.response_schema.split("/").at(-1)], `${endpoint.operation_id} response schema`);
for (const endpoint of api.prototype_read_routes) assert(api.$defs[endpoint.request_schema.split("/").at(-1)], `${endpoint.operation_id} request schema`);
for (const group of api.domain_groups) for (const entity of group.entities) {
  const schema = api.$defs[entity];
  assert(schema, `domain entity ${entity}`);
  assert.equal(schema.type, "object", `domain entity ${entity} must be an object contract`);
  assert.equal(schema.additionalProperties, false, `domain entity ${entity} must refuse undeclared fields`);
  assert(schema.required.length >= 3, `domain entity ${entity} must declare Phase 0 fields`);
}
assert.equal(api.$defs.CallCandidate.properties.evidence_excerpt["x-maxWords"], 15, "evidence word limit");

for (const [machineName, machine] of Object.entries(stateContract.machines)) {
  const states = new Set(machine.states);
  const terminal = new Set(machine.terminal);
  for (const transition of machine.allowed) {
    const from = Array.isArray(transition.from) ? transition.from : [transition.from];
    assert(!from.includes("*"), `${machineName} allowed wildcard`);
    for (const state of from) {
      assert(states.has(state), `${machineName} source ${state}`);
      assert(!terminal.has(state), `${machineName} terminal source ${state}`);
    }
    assert(states.has(transition.to), `${machineName} destination ${transition.to}`);
  }
}
exact(api.$defs.CallSummary.properties.processing_state.enum, stateContract.machines.call_session.states, "call lifecycle schema/machine");

const expectedSurfaces = fixtureContract.required_surfaces;
const expectedStates = fixtureContract.required_states;
exact(fixtures.map(fixture => fixture.surface), expectedSurfaces, "fixture surfaces");
const topKeys = ["schema_version", "surface", "requirement_ids", "synthetic", "states"];
const stateKeys = {
  "call-review": { normal: ["freshness", "call"] },
  "command-center": { normal: ["freshness", "headline", "items", "metrics"], default: ["freshness", "message", "items"] },
  "deal-room": { normal: ["freshness", "record", "parking_reasons"], partial: ["freshness", "message", "record"], conflict: ["freshness", "message", "conflict"] },
  "doc-request": { normal: ["freshness", "context", "answer", "request"] },
  "lead-board": { normal: ["freshness", "items"] },
  marketing: { normal: ["freshness", "items"] },
  more: { normal: ["freshness", "destinations", "registry_only"] },
  notifications: { normal: ["freshness", "items"] },
  tour: { normal: ["freshness", "tour"], offline: ["freshness", "message", "tour"] }
};
for (const fixture of fixtures) {
  exact(Object.keys(fixture), topKeys, `${fixture.surface} top keys`);
  assert.equal(fixture.schema_version, "workspace-fixture/v1");
  assert.equal(fixture.synthetic, true);
  exact(Object.keys(fixture.states), expectedStates, `${fixture.surface} states`);
  for (const state of expectedStates) {
    const expected = stateKeys[fixture.surface][state] || stateKeys[fixture.surface].default || (state === "normal" ? stateKeys[fixture.surface].normal : ["freshness", "message"]);
    exact(Object.keys(fixture.states[state]), expected, `${fixture.surface}.${state} keys`);
    assert(fixture.states[state].freshness, `${fixture.surface}.${state}.freshness`);
  }
}

console.log(JSON.stringify({ok: true, contracts: Object.keys(contracts).length, fixtures: fixtures.length, states: expectedStates.length, requirements: requirementIds.size, aliases: aliases.size, test_catalog: testIds.size, future_gates: futureGateIds.size, phase0_executable_tests: testIds.size - futureGateIds.size, events: eventIds.size, council_ids: councilIds.size, baselines: baselineIds.size, machines: Object.keys(stateContract.machines).length, endpoints: api.prototype_read_routes.length, domain_groups: api.domain_groups.length}, null, 2));
