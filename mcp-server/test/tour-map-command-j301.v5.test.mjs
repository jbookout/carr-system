// V5-J301 — the typed map-command contract, proved case by case.
//
// The one property this suite exists to establish, from Q124.D2:
//
//   A CLICK ON THE MAP AND A SENTENCE TO DOC ARE THE SAME COMMAND.
//
// It is proved the only way that means anything: the two origins are normalized
// independently, and their digests are compared. Byte-identical intent gives
// one digest; any difference in a governed argument gives two, and the suite
// walks every governed argument of every command to show the comparison is
// actually sensitive rather than agreeable. Origin is NOT part of the identity,
// and the suite proves that by moving the origin and watching the digest hold
// still.
//
// It also proves the refusals that keep the equivalence honest: no admission
// without the map contract's independent receipt, no navigation handoff without
// the human promotion receipt, no Journey 1 map command, no caller-supplied
// verdict, and no second path to governed state — every command names a verb
// that really exists in the deployed record-layer registry.
//
// Everything here is synthetic. No network, no provider, no database, no map.

import test from "node:test";
import assert from "node:assert/strict";

import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import { TOOLS } from "../src/tools.js";
import * as commands from "../src/tour-map-command-j301.v5.js";
import {
  V5J301CommandError,
  V5_J301_COMMAND_AXES,
  V5_J301_COMMAND_DECISIONS,
  V5_J301_COMMAND_NAMES,
  V5_J301_COMMAND_ORIGINS,
  V5_J301_COMMAND_PERMITTED_JOURNEY,
  V5_J301_COMMAND_PUBLIC_SURFACE,
  V5_J301_COMMAND_INTENDED_VERBS,
  V5_J301_COMMAND_VERB_ADAPTER_SEAM,
  V5_J301_COMMANDS_WITHOUT_A_VERB,
  V5_J301_PROMOTION_RECEIPT_AUTHORITY,
  V5_J301_PROMOTION_RECEIPT_READER_SEAM,
  V5_J301_MAP_COMMANDS,
  V5_J301_NAVIGATION_PLATFORMS,
  V5_J301_POSITION_ROLES,
  V5_J301_TRAVEL_MODES,
  V5_NO_EFFECTS,
  compareMapCommandOrigins,
  evaluateMapCommandAdmission,
  normalizeMapCommand,
  v5J301CommandPolicyCanonicalBytes,
  v5J301CommandPolicyDigest,
  v5J301MapCommandProjection,
} from "../src/tour-map-command-j301.v5.js";
import {
  V5_J301_CALLER_AUTHORITY_FIELDS,
  V5_J301_MAP_CONTRACT_GATE,
  V5_J301_MAP_CONTRACT_RECEIPT_STEP,
} from "../src/tour-workflow-j301.v5.js";

const TOUR = "tour-j301-fixture-0001";

/** One well-formed argument set per command, used from both origins. */
const FIXTURE_ARGUMENTS = Object.freeze({
  set_entrance_coordinate: {
    property_id: "property-j301-fixture-0001",
    latitude: 28.5383,
    longitude: -81.3792,
    position_role: "entrance",
  },
  reorder_route_stops: {
    tour_id: TOUR,
    base_route_version_id: "route-version-j301-fixture-0001",
    expected_route_version: 3,
    ordered_route_stop_ids: ["stop-fixture-a", "stop-fixture-b", "stop-fixture-c"],
  },
  set_selection_cart: {
    tour_id: TOUR,
    selected_property_ids: ["property-fixture-a", "property-fixture-b"],
  },
  hand_off_native_navigation: {
    route_version_id: "route-version-j301-fixture-0001",
    route_stop_id: "stop-fixture-a",
    platform: "google_maps",
    travel_mode: "driving",
  },
});

const EVERY_RESULT = [];
function record(value) {
  EVERY_RESULT.push(value);
  return value;
}

function normalize(command, origin, overrides = {}) {
  return record(normalizeMapCommand({
    organization_tenant_id: ORGANIZATION_TENANT_ID,
    journey: V5_J301_COMMAND_PERMITTED_JOURNEY,
    origin,
    command,
    arguments: { ...FIXTURE_ARGUMENTS[command], ...overrides },
  }));
}

// ---------------------------------------------------------------------------
// The equivalence.
// ---------------------------------------------------------------------------

test("a map click and a Doc sentence expressing one intent are one command", () => {
  for (const command of V5_J301_COMMAND_NAMES) {
    const fromMap = normalize(command, "clickable_map");
    const fromDoc = normalize(command, "doc_command");
    assert.equal(fromMap.command_digest, fromDoc.command_digest, command);

    const comparison = record(compareMapCommandOrigins({
      clickable_map: fromMap, doc_command: fromDoc,
    }));
    assert.equal(comparison.equivalent, true, command);
    assert.equal(comparison.command_digests_match, true, command);
    assert.equal(comparison.names_same_intended_verb, true, command);
    // EQUIVALENT is not APPLIED. Said on every comparison.
    assert.equal(comparison.governed_state_equivalent, false, command);
    assert.equal(comparison.governed_state_equivalence_reason_id, "no_command_is_applied_here");
    assert.equal(comparison.verb_adapter_bound, false, command);
    assert.equal(comparison.intended_verb, V5_J301_MAP_COMMANDS[command].intended_verb);
    assert.deepEqual(comparison.divergences, []);
    // And it is still not permission.
    assert.equal(comparison.admission, "unavailable", command);
    assert.equal(comparison.governed_state_applied, false, command);
  }
});

test("the origin is not part of the command's identity", () => {
  const fromMap = normalize("set_selection_cart", "clickable_map");
  const fromDoc = normalize("set_selection_cart", "doc_command");
  assert.notEqual(fromMap.origin, fromDoc.origin);
  assert.equal(fromMap.command_digest, fromDoc.command_digest);
  assert.equal(JSON.stringify(fromMap.command_preimage), JSON.stringify(fromDoc.command_preimage));
  assert.equal("origin" in fromMap.command_preimage, false);
});

test("every governed argument of every command actually moves the digest", () => {
  const moved = {
    set_entrance_coordinate: {
      property_id: "property-j301-fixture-0002",
      latitude: 28.5384,
      longitude: -81.3793,
      position_role: "driveway",
    },
    reorder_route_stops: {
      tour_id: "tour-j301-fixture-0002",
      base_route_version_id: "route-version-j301-fixture-0002",
      expected_route_version: 4,
      ordered_route_stop_ids: ["stop-fixture-c", "stop-fixture-b", "stop-fixture-a"],
    },
    set_selection_cart: {
      tour_id: "tour-j301-fixture-0002",
      selected_property_ids: ["property-fixture-b", "property-fixture-a"],
    },
    hand_off_native_navigation: {
      route_version_id: "route-version-j301-fixture-0002",
      route_stop_id: "stop-fixture-b",
      platform: "apple_maps",
      travel_mode: "walking",
    },
  };
  for (const command of V5_J301_COMMAND_NAMES) {
    const base = normalize(command, "clickable_map");
    for (const argument of V5_J301_MAP_COMMANDS[command].governed_arguments) {
      const variant = normalize(command, "clickable_map", { [argument]: moved[command][argument] });
      assert.notEqual(variant.command_digest, base.command_digest, `${command}.${argument}`);

      const comparison = record(compareMapCommandOrigins({
        clickable_map: base,
        doc_command: normalize(command, "doc_command", { [argument]: moved[command][argument] }),
      }));
      assert.equal(comparison.equivalent, false, `${command}.${argument}`);
      assert.deepEqual(comparison.divergences.map(entry => entry.field), [argument]);
    }
  }
});

test("a stop order that differs only in order is a different command", () => {
  const forward = normalize("reorder_route_stops", "clickable_map");
  const reversed = normalize("reorder_route_stops", "doc_command", {
    ordered_route_stop_ids: ["stop-fixture-c", "stop-fixture-b", "stop-fixture-a"],
  });
  assert.notEqual(forward.command_digest, reversed.command_digest);
});

test("two different commands are not equivalent, and the divergence says so", () => {
  const comparison = record(compareMapCommandOrigins({
    clickable_map: normalize("set_selection_cart", "clickable_map"),
    doc_command: normalize("reorder_route_stops", "doc_command"),
  }));
  assert.equal(comparison.equivalent, false);
  assert.equal(comparison.names_same_intended_verb, false);
  assert.equal(comparison.intended_verb, null);
  assert.deepEqual(comparison.divergences.map(entry => entry.field), ["command"]);
});

test("an envelope carrying a hand-edited digest is compared on what it says", () => {
  const honest = normalize("set_selection_cart", "clickable_map");
  const forged = {
    ...honest,
    command_digest: `sha256:${"0".repeat(64)}`,
    governed_arguments: { ...honest.governed_arguments, tour_id: "tour-j301-fixture-0002" },
  };
  const comparison = record(compareMapCommandOrigins({
    clickable_map: forged, doc_command: normalize("set_selection_cart", "doc_command"),
  }));
  assert.equal(comparison.equivalent, false);
  assert.deepEqual(comparison.divergences.map(entry => entry.field), ["tour_id"]);
  // Re-derived, so the forged digest never appears in the answer.
  assert.notEqual(comparison.clickable_map_command_digest, forged.command_digest);
});

test("the two origins must arrive in their own slots", () => {
  assert.throws(() => compareMapCommandOrigins({
    clickable_map: normalize("set_selection_cart", "doc_command"),
    doc_command: normalize("set_selection_cart", "doc_command"),
  }), error => error instanceof V5J301CommandError && error.code === "origin_mismatch");
});

// ---------------------------------------------------------------------------
// The refusals.
// ---------------------------------------------------------------------------

test("an active Journey 1 map command fails", () => {
  assert.throws(() => normalizeMapCommand({
    organization_tenant_id: ORGANIZATION_TENANT_ID,
    journey: "product_journey_1",
    origin: "clickable_map",
    command: "set_selection_cart",
    arguments: FIXTURE_ARGUMENTS.set_selection_cart,
  }), error => error.code === "j1_map_command_refused");
});

test("a caller-supplied verdict cannot be read at either level", () => {
  // The refusal here is the CLOSED SCHEMA, not a name scan: the request has
  // exactly five keys and a command has exactly the arguments its registry
  // entry declares, so every authority field is an unknown field. Asserted as
  // the exact code rather than as "one of two", because "either error is fine"
  // is how an unreachable check hides behind a reachable one.
  for (const field of V5_J301_CALLER_AUTHORITY_FIELDS) {
    assert.throws(() => normalizeMapCommand({
      organization_tenant_id: ORGANIZATION_TENANT_ID,
      journey: V5_J301_COMMAND_PERMITTED_JOURNEY,
      origin: "clickable_map",
      command: "set_selection_cart",
      arguments: FIXTURE_ARGUMENTS.set_selection_cart,
      [field]: { status: "pass" },
    }), error => error instanceof V5J301CommandError && error.code === "unknown_field", field);

    assert.throws(() => normalizeMapCommand({
      organization_tenant_id: ORGANIZATION_TENANT_ID,
      journey: V5_J301_COMMAND_PERMITTED_JOURNEY,
      origin: "clickable_map",
      command: "set_selection_cart",
      arguments: { ...FIXTURE_ARGUMENTS.set_selection_cart, [field]: true },
    }), error => error.code === "unknown_field", field);
  }
});

test("an unregistered command, origin, argument or role cannot be read", () => {
  const base = {
    organization_tenant_id: ORGANIZATION_TENANT_ID,
    journey: V5_J301_COMMAND_PERMITTED_JOURNEY,
    origin: "clickable_map",
    command: "set_entrance_coordinate",
    arguments: FIXTURE_ARGUMENTS.set_entrance_coordinate,
  };
  assert.throws(() => normalizeMapCommand({ ...base, command: "delete_route" }),
    error => error.code === "unknown_command");
  assert.throws(() => normalizeMapCommand({ ...base, origin: "background_agent" }),
    error => error.code === "unknown_origin");
  assert.throws(() => normalizeMapCommand({ ...base, journey: "product_journey_9" }),
    error => error.code === "unknown_journey");
  assert.throws(() => normalizeMapCommand({
    ...base, arguments: { ...base.arguments, position_role: "parking_lot_guess" },
  }), error => error.code === "unknown_position_role");
  assert.throws(() => normalizeMapCommand({
    ...base, arguments: { ...base.arguments, extra_hint: "please" },
  }), error => error.code === "unknown_field");
  const missing = { ...base.arguments };
  delete missing.latitude;
  assert.throws(() => normalizeMapCommand({ ...base, arguments: missing }),
    error => error.code === "missing_field");
});

test("a coordinate that arrives as a string is refused rather than coerced", () => {
  const base = {
    organization_tenant_id: ORGANIZATION_TENANT_ID,
    journey: V5_J301_COMMAND_PERMITTED_JOURNEY,
    origin: "doc_command",
    command: "set_entrance_coordinate",
    arguments: FIXTURE_ARGUMENTS.set_entrance_coordinate,
  };
  for (const bad of ["28.5383", Number.NaN, 91, -91]) {
    assert.throws(() => normalizeMapCommand({ ...base, arguments: { ...base.arguments, latitude: bad } }),
      error => error.code === "invalid_coordinate", String(bad));
  }
  for (const bad of ["-81.3792", 181, -181]) {
    assert.throws(() => normalizeMapCommand({ ...base, arguments: { ...base.arguments, longitude: bad } }),
      error => error.code === "invalid_coordinate", String(bad));
  }
});

test("a repeated stop in one route order is refused", () => {
  assert.throws(() => normalizeMapCommand({
    organization_tenant_id: ORGANIZATION_TENANT_ID,
    journey: V5_J301_COMMAND_PERMITTED_JOURNEY,
    origin: "clickable_map",
    command: "reorder_route_stops",
    arguments: {
      ...FIXTURE_ARGUMENTS.reorder_route_stops,
      ordered_route_stop_ids: ["stop-fixture-a", "stop-fixture-a"],
    },
  }), error => error.code === "duplicate_entry");
});

test("the tenant is the tenant, whatever the caller says", () => {
  assert.throws(() => normalizeMapCommand({
    organization_tenant_id: "some-other-tenant",
    journey: V5_J301_COMMAND_PERMITTED_JOURNEY,
    origin: "clickable_map",
    command: "set_selection_cart",
    arguments: FIXTURE_ARGUMENTS.set_selection_cart,
  }), error => error.code === "tenant_mismatch");
});

// ---------------------------------------------------------------------------
// Admission: always no, in exactly two shapes.
// ---------------------------------------------------------------------------

test("no command is admissible, and each says which receipt it is waiting on", () => {
  for (const command of V5_J301_COMMAND_NAMES) {
    for (const origin of V5_J301_COMMAND_ORIGINS) {
      const result = record(evaluateMapCommandAdmission({ envelope: normalize(command, origin) }));
      assert.ok(V5_J301_COMMAND_DECISIONS.includes(result.decision), command);
      assert.equal(result.governed_state_applied, false, command);
      assert.deepEqual(result.effects, V5_NO_EFFECTS, command);
      assert.equal(result.map_contract_gate, V5_J301_MAP_CONTRACT_GATE);
      assert.equal(result.map_contract_production_status,
        "approved_architecture_not_implemented_in_production");
      // Said on every answer, whichever shape of no it is.
      assert.equal(result.verb_adapter_bound, false, command);
      assert.equal(result.verb_adapter_seam, V5_J301_COMMAND_VERB_ADAPTER_SEAM, command);
      if (command === "hand_off_native_navigation") {
        assert.equal(result.decision, "refused");
        assert.equal(result.reason_id, "promotion_receipt_reader_unavailable");
        assert.ok(result.owed_seams.includes(V5_J301_PROMOTION_RECEIPT_READER_SEAM));
        // THE CORRECTION A REVIEWER FORCED: the store is real, and the refusal
        // names it rather than claiming it is missing.
        assert.equal(result.promotion_receipt_authority.store_exists_here, true);
        assert.equal(result.promotion_receipt_authority.reader_exists_here, false);
        assert.equal(result.promotion_receipt_authority.table, "ops.tour_map_promotion_receipt");
        assert.equal(result.promotion_receipt_authority.writer_verb,
          "record-tour-map-promotion-receipt");
      } else {
        assert.equal(result.decision, "unavailable");
        assert.equal(result.reason_id, "map_contract_receipt_unavailable");
        assert.deepEqual([...result.owed_seams],
          [V5_J301_COMMAND_VERB_ADAPTER_SEAM, V5_J301_MAP_CONTRACT_RECEIPT_STEP]);
      }
    }
  }
});

test("navigation is the one command that needs a human promotion receipt", () => {
  const needing = V5_J301_COMMAND_NAMES.filter(
    name => V5_J301_MAP_COMMANDS[name].requires_human_promotion_receipt);
  assert.deepEqual(needing, ["hand_off_native_navigation"]);
});

// ---------------------------------------------------------------------------
// The registry against the deployed record layer.
// ---------------------------------------------------------------------------

test("there is exactly one command family per axis Q124.D2 names", () => {
  assert.deepEqual([...V5_J301_COMMAND_AXES], ["coordinate", "route", "selection", "navigation"]);
  const axes = V5_J301_COMMAND_NAMES.map(name => V5_J301_MAP_COMMANDS[name].axis).sort();
  assert.deepEqual(axes, [...V5_J301_COMMAND_AXES].sort());
});

test("every verb a command NAMES resolves in the deployed registry", () => {
  // Three commands name a verb; navigation names none, because the nearest
  // deployed verb writes a human promotion decision rather than performing a
  // handoff. Naming that one would have made a receipt write look like
  // navigation, which is the claim a reviewer rejected.
  assert.equal(V5_J301_COMMAND_INTENDED_VERBS.length, 3);
  for (const verb of V5_J301_COMMAND_INTENDED_VERBS) {
    assert.ok(Object.hasOwn(TOOLS, verb), `${verb} is not a deployed verb`);
  }
  assert.deepEqual([...V5_J301_COMMANDS_WITHOUT_A_VERB], ["hand_off_native_navigation"]);
  // The control: an invented verb fails the same check.
  assert.equal(Object.hasOwn(TOOLS, "apply-tour-map-command"), false);
});

/**
 * THE GAP, CHECKED AGAINST THE REAL inputSchema OF THE REAL VERB.
 *
 * The claim this replaces was that every command "traverses" a deployed verb.
 * It does not: it names one, and the arguments it emits do not satisfy that
 * verb's own schema. Rather than assert compatibility that is not there, each
 * registry entry RECORDS the gap, and this test reads the deployed verb's
 * `inputSchema.required` and `properties` straight out of tools.js and asserts
 * the recorded gap is exactly right. A verb that gains or loses a required
 * field fails here, so the gap cannot silently go stale.
 */
test("each command's recorded verb gap is exactly what the deployed schema says", () => {
  let checked = 0;
  for (const name of V5_J301_COMMAND_NAMES) {
    const contract = V5_J301_MAP_COMMANDS[name];
    if (contract.intended_verb === null) {
      assert.deepEqual([...contract.verb_required_not_supplied], []);
      assert.deepEqual([...contract.supplied_not_in_verb_schema], []);
      continue;
    }
    const schema = TOOLS[contract.intended_verb].inputSchema;
    const required = [...schema.required].sort();
    const properties = Object.keys(schema.properties);
    const supplied = [...contract.governed_arguments];

    const missing = required.filter(field => !supplied.includes(field));
    assert.deepEqual([...contract.verb_required_not_supplied].sort(), missing,
      `${name}: the recorded missing-field list is not what ${contract.intended_verb} requires`);
    const unknown = supplied.filter(field => !properties.includes(field));
    assert.deepEqual([...contract.supplied_not_in_verb_schema].sort(), unknown.sort(),
      `${name}: the recorded unknown-field list is not what ${contract.intended_verb} accepts`);
    // And the point of recording it: the gap is never empty, so no command is
    // one rename away from looking like a call.
    assert.ok(missing.length > 0,
      `${name} now satisfies ${contract.intended_verb}; the adapter claim can be revisited`);
    checked++;
  }
  assert.equal(checked, 3);
});

test("an envelope says the adapter is unbound and carries the gap", () => {
  for (const name of V5_J301_COMMAND_NAMES) {
    const envelope = record(normalize(name, "doc_command"));
    assert.equal(envelope.verb_adapter_bound, false, name);
    assert.equal(envelope.verb_adapter_seam, V5_J301_COMMAND_VERB_ADAPTER_SEAM, name);
    assert.deepEqual([...envelope.verb_argument_gap.required_by_verb_not_supplied],
      [...V5_J301_MAP_COMMANDS[name].verb_required_not_supplied], name);
  }
});

test("the promotion receipt store is named as existing, because it does", () => {
  // migrations/0430_tour_delivery_data_plane.sql creates ops.tour_map_promotion_receipt,
  // db/schema.sql carries it, and record-tour-map-promotion-receipt is the
  // deployed humanOnly verb that writes it. The earlier draft of this slice
  // said the store did not exist; it did.
  assert.equal(V5_J301_PROMOTION_RECEIPT_AUTHORITY.store_exists_here, true);
  assert.equal(V5_J301_PROMOTION_RECEIPT_AUTHORITY.reader_exists_here, false);
  const verb = V5_J301_PROMOTION_RECEIPT_AUTHORITY.writer_verb;
  assert.ok(Object.hasOwn(TOOLS, verb), `${verb} is not a deployed verb`);
  assert.equal(TOOLS[verb].humanOnly, true);
  assert.equal(V5_J301_PROMOTION_RECEIPT_AUTHORITY.writer_verb_is_human_only, true);
});

test("the vocabularies are the ones the map doctrine names", () => {
  assert.deepEqual([...V5_J301_POSITION_ROLES],
    ["entrance", "driveway", "parking_access", "start", "end"]);
  assert.deepEqual([...V5_J301_NAVIGATION_PLATFORMS], ["apple_maps", "google_maps"]);
  assert.deepEqual([...V5_J301_TRAVEL_MODES], ["driving", "walking"]);
});

// ---------------------------------------------------------------------------
// The public surface.
// ---------------------------------------------------------------------------

const PRIVILEGED = Object.freeze([
  "allow", "allowed", "commit", "committed", "prompt", "suppress", "release",
  "covered", "drafted", "proposed", "queued", "healthy", "passing", "passable",
  "green", "admitted", "accepted", "approved", "authorized", "granted", "applied",
  "published", "shared", "rendered", "issued",
]);

function privilegedHit(value, key = "", depth = 0) {
  if (depth > 12) return null;
  if (PRIVILEGED.includes(key)) return key;
  if (typeof value === "string") return PRIVILEGED.includes(value) ? value : null;
  if (Array.isArray(value)) {
    for (const entry of value) {
      const hit = privilegedHit(entry, key, depth + 1);
      if (hit) return hit;
    }
    return null;
  }
  if (value && typeof value === "object") {
    for (const [inner, entry] of Object.entries(value)) {
      const hit = privilegedHit(entry, inner, depth + 1);
      if (hit) return hit;
    }
  }
  return null;
}

test("the privileged-string detector actually fires", () => {
  assert.equal(privilegedHit({ admission: "granted" }), "granted");
  assert.equal(privilegedHit({ deep: [{ state: "published" }] }), "published");
  assert.equal(privilegedHit({ admission: "unavailable" }), null);
});

test("no public export yields a privileged outcome from any caller-controlled input", () => {
  let swept = 0;
  for (const command of V5_J301_COMMAND_NAMES) {
    for (const origin of V5_J301_COMMAND_ORIGINS) {
      for (const journey of ["product_journey_1", V5_J301_COMMAND_PERMITTED_JOURNEY]) {
        let envelope;
        try {
          envelope = normalizeMapCommand({
            organization_tenant_id: ORGANIZATION_TENANT_ID,
            journey, origin, command, arguments: FIXTURE_ARGUMENTS[command],
          });
        } catch (error) {
          assert.ok(error instanceof V5J301CommandError);
          continue;
        }
        swept++;
        assert.equal(envelope.admission, "unavailable");
        assert.equal(privilegedHit(envelope), null, JSON.stringify(envelope).slice(0, 200));
        const admission = evaluateMapCommandAdmission({ envelope });
        assert.equal(privilegedHit(admission), null, JSON.stringify(admission).slice(0, 200));
        record(envelope);
        record(admission);
      }
    }
  }
  assert.ok(swept >= 8, `expected the full matrix, swept ${swept}`);
  for (const fn of [v5J301MapCommandProjection, v5J301CommandPolicyDigest]) {
    assert.equal(privilegedHit(record(fn())), null, fn.name);
  }
});

test("every result this suite produced reports no effect and no applied state", () => {
  assert.ok(EVERY_RESULT.length > 40, `swept ${EVERY_RESULT.length} results`);
  for (const result of EVERY_RESULT) {
    if (result && typeof result === "object") {
      if ("effects" in result) assert.deepEqual(result.effects, V5_NO_EFFECTS);
      if ("governed_state_applied" in result) assert.equal(result.governed_state_applied, false);
      if ("admission" in result) assert.equal(result.admission, "unavailable");
      assert.equal(privilegedHit(result), null);
    }
  }
});

test("the module's real exports are exactly its declared public surface", () => {
  assert.deepEqual(Object.keys(commands).sort(), [...V5_J301_COMMAND_PUBLIC_SURFACE].sort());
});

test("the projection tells the same story the evaluators do", () => {
  const projection = record(v5J301MapCommandProjection());
  assert.equal(projection.origin_is_part_of_command_identity, false);
  assert.equal(projection.journey_one_map_commands_permitted, false);
  assert.equal(projection.navigation_handoff_reachable_today, false);
  assert.equal(projection.admission_reachable_today, false);
  assert.equal(projection.admission_reason_id, "map_contract_receipt_unavailable");
  assert.equal(projection.navigation_handoff_reason_id, "promotion_receipt_reader_unavailable");
  // THE CLAIM THAT WAS WRONG, and the narrower ones that replaced it.
  assert.equal(projection.every_command_traverses_a_deployed_verb, false);
  assert.equal(projection.every_named_verb_resolves_in_the_deployed_registry, true);
  assert.equal(projection.commands_with_a_bound_verb_adapter, 0);
  assert.deepEqual([...projection.commands_naming_no_verb], ["hand_off_native_navigation"]);
  assert.equal(projection.verb_adapter_seam, V5_J301_COMMAND_VERB_ADAPTER_SEAM);
  assert.equal(projection.promotion_receipt_authority.store_exists_here, true);
  assert.equal(projection.promotion_receipt_authority.reader_exists_here, false);
  // The projection's per-command gaps are the registry's, not a second copy.
  for (const name of V5_J301_COMMAND_NAMES) {
    assert.deepEqual([...projection.verb_argument_gaps[name].required_by_verb_not_supplied],
      [...V5_J301_MAP_COMMANDS[name].verb_required_not_supplied], name);
  }
  assert.deepEqual([...projection.command_intended_verbs], [...V5_J301_COMMAND_INTENDED_VERBS]);
  assert.equal(projection.public_projection_here, false);
  assert.equal(projection.share_grant_issuance_here, false);
  assert.equal(projection.pdf_render_request_here, false);
  assert.match(v5J301CommandPolicyDigest(), /^sha256:[0-9a-f]{64}$/);
  assert.equal(v5J301CommandPolicyCanonicalBytes(),
    JSON.stringify(JSON.parse(v5J301CommandPolicyCanonicalBytes())));
});
