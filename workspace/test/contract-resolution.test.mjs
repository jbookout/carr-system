import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import { join } from "node:path";
import test from "node:test";
import { futureGateIds, testRegistry } from "./id-registry.mjs";
import { compileSchema } from "../contracts/schema-validator.mjs";

const workspace = new URL("../", import.meta.url);
const readJson = async relative => JSON.parse(await readFile(new URL(relative, workspace), "utf8"));
const readText = relative => readFile(new URL(relative, workspace), "utf8");
const exactKeys = (value, keys, label) => assert.deepEqual(Object.keys(value).sort(), [...keys].sort(), label);

async function loadFixtures() {
  const directory = new URL("fixtures/", workspace);
  const names = (await readdir(directory)).filter(name => name.endsWith(".v1.json"));
  return new Map(await Promise.all(names.map(async name => {
    const fixture = JSON.parse(await readFile(join(directory.pathname, name), "utf8"));
    return [fixture.surface, fixture];
  })));
}

test("all fixture twins conform to the finalized exact-key contract", async () => {
  const [contract, fixtures] = await Promise.all([readJson("contracts/prototype-fixture-contract.v1.json"), loadFixtures()]);
  const validateFixture = compileSchema(contract);
  assert.deepEqual([...fixtures.keys()].sort(), [...contract.required_surfaces].sort());
  const normalKeys = {
    "call-review": ["freshness", "call"],
    "command-center": ["freshness", "headline", "items", "metrics"],
    "deal-room": ["freshness", "record", "parking_reasons"],
    "doc-request": ["freshness", "context", "answer", "request"],
    "lead-board": ["freshness", "items"],
    marketing: ["freshness", "items"],
    more: ["freshness", "destinations", "registry_only"],
    notifications: ["freshness", "items"],
    tour: ["freshness", "tour"]
  };
  for (const [surface, fixture] of fixtures) {
    const validation = validateFixture(fixture);
    assert.equal(validation.valid, true, `${surface}: ${validation.errors.join("; ")}`);
    exactKeys(fixture, ["schema_version", "surface", "requirement_ids", "synthetic", "states"], `${surface} top-level`);
    assert.equal(fixture.schema_version, "workspace-fixture/v1");
    assert.equal(fixture.synthetic, true);
    exactKeys(fixture.states, contract.required_states, `${surface} state set`);
    for (const [state, value] of Object.entries(fixture.states)) {
      exactKeys(value.freshness, ["status", "as_of"], `${surface}:${state} freshness`);
      if (state === "normal") exactKeys(value, normalKeys[surface], `${surface}:normal`);
      else if (surface === "command-center") exactKeys(value, ["freshness", "message", "items"], `${surface}:${state}`);
      else if (surface === "deal-room" && state === "partial") exactKeys(value, ["freshness", "message", "record"], `${surface}:${state}`);
      else if (surface === "deal-room" && state === "conflict") exactKeys(value, ["freshness", "message", "conflict"], `${surface}:${state}`);
      else if (surface === "tour" && state === "offline") exactKeys(value, ["freshness", "message", "tour"], `${surface}:${state}`);
      else exactKeys(value, ["freshness", "message"], `${surface}:${state}`);
    }
  }
});

test("fixture requirement IDs resolve to traceability requirements", async () => {
  const [traceability, fixtures] = await Promise.all([readJson("contracts/phase0-traceability.v1.json"), loadFixtures()]);
  const requirements = new Set(traceability.entries.map(entry => entry.id));
  for (const fixture of fixtures.values()) {
    for (const id of fixture.requirement_ids) assert.ok(requirements.has(id), `${fixture.surface} unresolved requirement ${id}`);
  }
});

test("every test ID resolves to either an executed check or an explicit future gate", async () => {
  const [traceability, threat, events, crossReference, environment] = await Promise.all([
    readJson("contracts/phase0-traceability.v1.json"),
    readJson("contracts/threat-model.v1.json"),
    readJson("contracts/notification-event-taxonomy.v1.json"),
    readJson("contracts/cross-reference-registry.v1.json"),
    readJson("contracts/environment-release-process.v1.json")
  ]);
  const referenced = new Set([
    ...traceability.entries.flatMap(entry => entry.tests),
    ...threat.threats.flatMap(item => item.tests),
    ...events.events.flatMap(event => event.test_ids)
  ]);
  assert.deepEqual([...referenced].sort(), [...crossReference.test_ids].sort(), "cross-reference test namespace drift");
  const classified = new Set([...testRegistry.keys(), ...futureGateIds]);
  assert.deepEqual([...classified].sort(), [...crossReference.test_ids].sort(), "test classification drift");
  const runtimeGates = new Map(environment.runtime_verification_gates.map(item => [item.test_id, item]));
  for (const id of referenced) {
    assert.notEqual(testRegistry.has(id), futureGateIds.has(id), `${id} must have exactly one classification`);
    if (futureGateIds.has(id)) {
      const gate = runtimeGates.get(id);
      assert.equal(gate?.status, "future_gate_not_implemented_in_phase0_static_prototype", `${id} future gate status`);
      assert.ok(gate.required_evidence.length >= 2, `${id} future evidence`);
    }
  }
});

test("every registered contract test executes against the prototype and planning evidence", async () => {
  const [fixtures, html, css, app, client, server, environment, manifest, council, trace, acceptance] = await Promise.all([
    loadFixtures(),
    readText("public/index.html"),
    readText("public/css/app.css"),
    readText("public/js/app.js"),
    readText("public/js/client.js"),
    readText("public/server.mjs"),
    readJson("contracts/environment-release-process.v1.json"),
    readJson("contracts/phase0-manifest.v1.json"),
    readJson("contracts/council-review-register.v1.json"),
    readJson("contracts/phase0-traceability.v1.json"),
    readJson("contracts/phase0-acceptance.v1.json")
  ]);
  const context = { fixtures, html, css, app, client, server, environment, manifest, council, trace, acceptance };
  for (const [id, check] of testRegistry) assert.doesNotThrow(() => check(context), id);
});

test("all baseline IDs render with unknown values and an instrumentation or interview action", async () => {
  const [acceptance, fixtures] = await Promise.all([readJson("contracts/phase0-acceptance.v1.json"), loadFixtures()]);
  const cutover = fixtures.get("more").states.normal.destinations.find(item => Array.isArray(item.baselines));
  assert.equal(cutover.text, acceptance.operating_objective_gate.question);
  assert.deepEqual(cutover.baselines.map(item => item.id), acceptance.baseline_measure_plan.map(item => item.id));
  for (const baseline of cutover.baselines) {
    assert.equal(baseline.value, null, baseline.id);
    assert.equal(baseline.status, "Unknown — not yet measured", baseline.id);
    assert.match(baseline.next_action, /interview|diary|journey|time|count/i, baseline.id);
  }
});

test("surface cutover owners and Front Door usage gate resolve without invented evidence", async () => {
  const [surfaceMap, crossReference, environment] = await Promise.all([
    readJson("contracts/surface-registry-migration-map.v1.json"),
    readJson("contracts/cross-reference-registry.v1.json"),
    readJson("contracts/environment-release-process.v1.json")
  ]);
  const assignments = new Map(surfaceMap.cutover_owner_assignments.map(item => [item.id, item]));
  assert.equal(assignments.size, 5);
  for (const surface of surfaceMap.surfaces.filter(item => item.cutover_owner.startsWith("OPEN-SURFACE-OWNER-"))) {
    const assignment = assignments.get(surface.cutover_owner);
    assert.equal(assignment.surface_id, surface.id, surface.cutover_owner);
    assert.ok(assignment.accountable && assignment.technical_verifier, surface.cutover_owner);
  }
  assert.ok(crossReference.test_ids.includes("FRONTDOOR-USAGE-001"));
  const gate = environment.runtime_verification_gates.find(item => item.test_id === "FRONTDOOR-USAGE-001");
  assert.equal(gate.status, "future_gate_not_implemented_in_phase0_static_prototype");
  assert.match(gate.required_evidence.join(" "), /usage inventory/i);
});

test("baseline presentation exposes the bound action and like-for-like comparison", async () => {
  const fixtures = await loadFixtures();
  const baselines = fixtures.get("more").states.normal.destinations.find(item => item.baselines).baselines;
  assert.ok(baselines.every(item => item.bound_product_action && item.post_launch_comparison));
  const app = await readText("public/js/app.js");
  assert.match(app, /Bound product action:/);
  assert.match(app, /Post-launch comparison:/);
});

test("glossary provenance and all six conceptual operating roles are explicit", async () => {
  const glossary = await readJson("contracts/domain-glossary.v1.json");
  assert.equal(glossary.source.source_sha256, "e4370cbafcec21906cd38ad529f15fba35b2d54db5568f8b762f29e7ff65662b");
  assert.equal(glossary.source.doctrine_document_id, "c7f31740-7f4b-47e9-ab93-c7f2854bacc6");
  assert.equal(glossary.source.doctrine_generation, 324);
  assert.equal(glossary.source.doctrine_section_count, 33);
  assert.equal(glossary.source.timing_section_key, "s23");
  assert.equal(glossary.source.timing_section_version, 3);
  const terms = new Set(glossary.terms.map(item => item.term));
  for (const role of ["CARR Workspace", "The Command Center", "CARR Control Room", "Doc", "Claude Code and Codex", "CARR record layer"]) assert.ok(terms.has(role), role);
});

test("integrated planning sources, milestone timing, predecessors, and cost bands stay harmonized", async () => {
  const [manifest, trace, council, acceptance] = await Promise.all([
    readJson("contracts/phase0-manifest.v1.json"),
    readJson("contracts/phase0-traceability.v1.json"),
    readJson("contracts/council-review-register.v1.json"),
    readJson("contracts/phase0-acceptance.v1.json")
  ]);
  const expectedIds = [
    "c7f31740-7f4b-47e9-ab93-c7f2854bacc6",
    "11fdc56f-9af5-47c9-92a7-bb392ca60bd6",
    "15d2250c-4821-4f83-9dc5-063f9470139d",
    "10d25f48-916b-4a7f-a1a6-d231274fed4b"
  ].sort();
  assert.deepEqual(manifest.canonical_planning_sources.map(item => item.document_id).sort(), expectedIds);
  assert.deepEqual(trace.canonical_planning_set.map(item => item.document_id).sort(), expectedIds);
  for (const id of expectedIds) assert.ok(council.review_event.inputs.some(item => item.includes(id)), id);
  assert.equal(manifest.source.doctrine_generation, 324);
  assert.equal(manifest.source.timing_section_version, 3);
  assert.equal(manifest.source.desktop_artifact_matches_current_doctrine_generation, "unverified_after_timing_section_revision");
  const program = manifest.integrated_delivery_program;
  assert.equal(program.mature_foundation_v1.target_date, "2026-10-05");
  assert.equal(program.workspace_web_timing.evidence_range, "12–16 weeks");
  assert.match(program.full_multi_platform_timing, /^4–6 months/);
  assert.deepEqual(program.pricing_evidence_bands_usd_per_month, {
    phase0_or_pilot_incremental: {min: 5, max: 20},
    mature_two_partner_web_operations: {min: 62, max: 84},
    before_paid_incident_response: {min: 28, max: 50},
    with_apple_reserve_approximate: {min: 70, max: 93},
    high_intentional_use: {min: 300, max: 500}
  });
  assert.match(acceptance.integrated_program_acceptance.construction_gate, /Joe approves.*council output/);
});

test("domain entities are complete typed objects rather than generic record aliases", async () => {
  const api = await readJson("contracts/business-entity-api-contracts.v1.json");
  for (const group of api.domain_groups) for (const entity of group.entities) {
    const schema = api.$defs[entity];
    assert.equal(schema.type, "object", entity);
    assert.equal(schema.additionalProperties, false, entity);
    assert.ok(schema.required.length >= 3, entity);
    assert.ok(schema.properties.record || ["CallSummary", "CallCandidate", "EngineeringRequest", "Tour", "Notification"].includes(entity), entity);
  }
  assert.ok(api.$defs.Tour.properties.device_sync.items.properties.state.enum.includes("superseded"));
});

test("JSON Schema not is enforced and unsupported validation keywords fail compilation", async () => {
  const api = await readJson("contracts/business-entity-api-contracts.v1.json");
  const validateTopic = compileSchema(api, api.$defs.Notification.properties.topic);
  assert.equal(validateTopic("tour_review_ready").valid, true);
  const forbidden = validateTopic("named_lead_outreach");
  assert.equal(forbidden.valid, false);
  assert.match(forbidden.errors.join(" "), /forbidden not schema/);
  assert.throws(() => compileSchema({ type: "string", minLenght: 1 }), /Unsupported JSON Schema keyword: minLenght/);
});

test("Tour prototype stages are canonical and pack phases cannot masquerade as lifecycle states", async () => {
  const [fixtureSchema, api, machines, fixtures] = await Promise.all([
    readJson("contracts/prototype-fixture-contract.v1.json"),
    readJson("contracts/business-entity-api-contracts.v1.json"),
    readJson("contracts/state-machines.v1.json"),
    loadFixtures()
  ]);
  const prototypeStages = fixtureSchema.$defs.TourPrototype.properties.stage.enum;
  const canonicalStages = api.$defs.Tour.properties.state.enum;
  assert.ok(prototypeStages.every(stage => canonicalStages.includes(stage)));
  assert.ok(prototypeStages.every(stage => machines.machines.tour.states.includes(stage)));
  assert.deepEqual(fixtureSchema.$defs.TourPrototypePack.properties.status.enum, api.$defs.Tour.properties.offline_pack.properties.state.enum);
  const validateFixture = compileSchema(fixtureSchema);
  for (const drift of ["route_confirmed", "touring", "offline", "review", "pack_downloading", "ready"]) {
    const candidate = structuredClone(fixtures.get("tour"));
    candidate.states.normal.tour.stage = drift;
    assert.equal(validateFixture(candidate).valid, false, drift);
  }
});
