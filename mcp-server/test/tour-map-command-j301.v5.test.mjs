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
// verdict, and no second path to governed state.
//
// AND IT PROVES THE VERB CLAIM AT ITS REAL STRENGTH, WHICH IS WEAKER THAN
// "TRAVERSED" AND NARROWER THAN "EVERY COMMAND". THREE of the four commands
// name a verb that exists in the deployed record-layer registry — and naming is
// all they do. The fourth, `hand_off_native_navigation`, names NO verb at all,
// because the nearest deployed verb writes a human promotion decision rather
// than performing a handoff, and naming it would have made a receipt write look
// like navigation. The suite asserts that count (three, not four) rather than a
// universal. There is no bound adapter either,
// so for each command that does name one the suite reads the verb's inputSchema and
// asserts the EXACT fields this slice cannot supply and the exact fields it
// would supply that the verb does not accept, then asserts that the gap is
// still open. A command that closed its gap would fail here rather than quietly
// look like a call.
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
import * as workflow from "../src/tour-workflow-j301.v5.js";

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
    assert.equal(comparison.same_command, true, command);
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
      assert.equal(comparison.same_command, false, `${command}.${argument}`);
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
  assert.equal(comparison.same_command, false);
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
  assert.equal(comparison.same_command, false);
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

test("every verb a command NAMES resolves in the deployed registry, and navigation names none", () => {
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

// ---------------------------------------------------------------------------
// THE PUBLIC-SURFACE SWEEP, OVER EVERY EXPORT OF BOTH MODULES.
//
// The guard above this one walked a hand-typed list of callables. A list is
// only as honest as whoever last edited it: an export added tomorrow is not on
// it, and nothing fails. So this sweep takes the namespaces themselves, walks
// `Object.keys` of each, and calls EVERY exported function with every
// caller-controlled shape the J301 suites already define — the four map-command
// requests from both origins and both journeys, the envelopes those produce,
// an origin pair, the workflow stage-action and Assignment-activity requests,
// the step-key request, a journal binding — plus null, undefined, empty object,
// empty array, empty string and zero. Whatever comes back is swept. A new
// export is swept the day it appears, whether or not anyone remembers this
// file.
//
// WHAT COUNTS AS A HIT, and why it is this and not a byte scan. Two rules, and
// no exemption list anywhere:
//
//   A. a NAME claim — an object key (or an export's own name) whose word
//      tokens contain the word, whose value is exactly `true`. This is how a
//      surface says `verified: true`, `read: true`, `present: true`.
//   B. a VALUE claim — a string, at any depth, that IS the word. This is how a
//      surface says `admission: "ok"` or `status: "complete"`.
//
// One structural distinction, and it is derived from the modules rather than
// typed here: a list the workflow module EXPORTS AS A REFUSAL VOCABULARY — the
// caller-authority fields, the forbidden activity fields, the refused intents,
// the Assignment phases a Tour may never set — is an enumeration of what is
// refused, not an answer. `V5_J301_CALLER_AUTHORITY_FIELDS` holds the literal
// strings "allow" and "verified" BECAUSE those are field names a caller must
// not be able to supply; a sweep that made the module stop naming them would
// delete the refusal it is testing. So a value is skipped only when it sits
// inside an array whose contents are exactly one of those exported
// vocabularies, which no hand-edit here can widen: change the module's refusal
// list and this follows it. Every other position, including every other array,
// is scanned.
//
// A blind substring scan over every byte of a return was tried first and is
// not implementable honestly: `v5J301CommandPolicyPreimage()` returns the
// SETTLED DECISION TEXT of Q124.D2, which contains "equivalent"; the workflow
// module's own stage is named `attended_mls_acquisition`; and both modules name
// a promotion-receipt READER seam whose absence is the reason the navigation
// handoff is unavailable. Passing such a scan would mean editing quoted
// doctrine and un-naming the missing seams — making the surface say LESS about
// what it cannot do, which is the opposite of the property under test. Naming a
// thing that does not exist is not claiming it. So the sweep bans the claim,
// not the noun, and the mutation controls below prove it still fires.
// ---------------------------------------------------------------------------

const FORBIDDEN_OUTCOME_WORDS = Object.freeze([
  "ok", "allow", "pass", "satisfied", "complete", "admitted",
  "resumed", "attended", "verified", "present", "read", "equivalent",
]);

/** camelCase and snake_case both fall apart into lowercase word tokens. */
function wordTokens(name) {
  return String(name)
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter(Boolean);
}

/**
 * Every hit of `word` in `value`, as a path, so a failure names the field
 * rather than dumping the envelope.
 */
function outcomeClaims(word, value, { path = "", key = "", depth = 0, into = [] } = {}) {
  if (depth > 12) return into;
  if (Array.isArray(value) && isDeclaredRefusalVocabulary(value)) return into;
  if (value === true && wordTokens(key).includes(word)) into.push(`${path} === true`);
  if (typeof value === "string" && value.toLowerCase() === word) into.push(`${path} === "${value}"`);
  if (Array.isArray(value)) {
    value.forEach((entry, index) =>
      outcomeClaims(word, entry, { path: `${path}[${index}]`, key, depth: depth + 1, into }));
  } else if (value && typeof value === "object") {
    for (const [inner, entry] of Object.entries(value)) {
      outcomeClaims(word, entry, { path: `${path}.${inner}`, key: inner, depth: depth + 1, into });
    }
  }
  return into;
}

/**
 * The refusal vocabularies the workflow module exports. Taken by value, so a
 * copy inside a projection or policy preimage is recognized as the same list.
 */
const REFUSAL_VOCABULARIES = Object.freeze([
  workflow.V5_J301_CALLER_AUTHORITY_FIELDS,
  workflow.V5_J301_FORBIDDEN_ACTIVITY_FIELDS,
  workflow.V5_J301_REFUSED_INTENTS,
  workflow.V5_J301_ASSIGNMENT_PHASES,
].map(vocabulary => [...vocabulary].join("\u0000")));

function isDeclaredRefusalVocabulary(array) {
  return array.every(entry => typeof entry === "string")
    && REFUSAL_VOCABULARIES.includes(array.join("\u0000"));
}

/** The caller-controlled shapes, all of them, including the degenerate ones. */
const CALLER_SHAPES = [];
{
  const envelopes = [];
  for (const command of V5_J301_COMMAND_NAMES) {
    for (const origin of V5_J301_COMMAND_ORIGINS) {
      for (const journey of commands.V5_J301_COMMAND_JOURNEYS) {
        const request = {
          organization_tenant_id: ORGANIZATION_TENANT_ID,
          journey, origin, command, arguments: FIXTURE_ARGUMENTS[command],
        };
        CALLER_SHAPES.push(request);
        try {
          const envelope = normalizeMapCommand(request);
          envelopes.push({ command, origin, envelope });
          CALLER_SHAPES.push(envelope, { envelope });
        } catch (error) {
          assert.ok(error instanceof V5J301CommandError);
        }
      }
    }
  }
  for (const command of V5_J301_COMMAND_NAMES) {
    const pair = {};
    for (const { command: name, origin, envelope } of envelopes) {
      if (name === command) pair[origin] = envelope;
    }
    if (pair.clickable_map && pair.doc_command) CALLER_SHAPES.push(pair);
  }
  // The workflow module's caller-controlled shapes, as its own suite writes them.
  CALLER_SHAPES.push({
    organization_tenant_id: ORGANIZATION_TENANT_ID,
    tour_id: TOUR,
    assignment_id: "assignment-j301-fixture-0001",
    stage: "attended_mls_acquisition",
    action_kind: "capture_listing_observation",
    declared_actor_slug: "joe",
    attended_intent: "human_present_for_this_action",
    actor_class: "human_attended",
    action_subject_digest: `sha256:${"a".repeat(64)}`,
  });
  CALLER_SHAPES.push({
    organization_tenant_id: ORGANIZATION_TENANT_ID,
    assignment_id: "assignment-j301-fixture-0001", tour_id: TOUR,
    activity_kind: "tour_created", declared_actor_slug: "joe",
    attended_intent: "human_present_for_this_action",
    occurred_at: "2026-09-11T14:30:00Z",
    activity_payload: { stop_count: 4, market: "fixture-market" },
  });
  CALLER_SHAPES.push({
    tour_id: TOUR, stage: "attended_mls_acquisition",
    action_kind: "capture_listing_observation",
    action_subject_digest: `sha256:${"a".repeat(64)}`,
  });
  CALLER_SHAPES.push({ tour_id: TOUR, assignment_id: "assignment-j301-fixture-0001" });
  CALLER_SHAPES.push({ decisions: { ...workflow.V5_J301_SETTLED_DECISIONS } });
  CALLER_SHAPES.push({
    organization_tenant_id: ORGANIZATION_TENANT_ID,
    tour_id: TOUR, assignment_id: "assignment-j301-fixture-0001",
    stage: "attended_mls_acquisition", action_kind: "capture_listing_observation",
    action_subject_digest: `sha256:${"a".repeat(64)}`,
    declared_actor_slug: "joe", recorded_at: "2026-09-11T14:00:00Z",
  });
  CALLER_SHAPES.push(null, undefined, {}, [], "", 0);
}

/**
 * Every export of both modules, and — for the functions — everything they hand
 * back for those shapes. Built once, swept once per word.
 */
const SURFACE = [];
const NAMESPACES = Object.freeze([
  ["tour-map-command-j301.v5.js", commands],
  ["tour-workflow-j301.v5.js", workflow],
]);
const EXPORTS_SEEN = new Set();
const FUNCTIONS_CALLED = new Set();
for (const [module, namespace] of NAMESPACES) {
  for (const name of Object.keys(namespace)) {
    const exported = namespace[name];
    EXPORTS_SEEN.add(`${module}#${name}`);
    SURFACE.push({ origin: `${module} export ${name}`, name, value: exported });
    if (typeof exported !== "function") continue;
    for (const shape of [...CALLER_SHAPES, undefined]) {
      let returned;
      try {
        returned = exported(shape);
      } catch {
        continue; // A refusal is not an outcome. Throwing is allowed; claiming is not.
      }
      FUNCTIONS_CALLED.add(`${module}#${name}`);
      SURFACE.push({ origin: `${module} ${name}() returned`, name: "", value: returned });
    }
  }
}

test("the sweep really does cover every export of both modules", () => {
  const declared = [...V5_J301_COMMAND_PUBLIC_SURFACE, ...workflow.V5_J301_PUBLIC_SURFACE];
  assert.equal(EXPORTS_SEEN.size, Object.keys(commands).length + Object.keys(workflow).length);
  assert.equal(EXPORTS_SEEN.size, declared.length,
    "an export exists that the module's own declared public surface does not list");
  // And the functions were actually entered, not merely enumerated.
  const callables = [...NAMESPACES].flatMap(([module, namespace]) =>
    Object.keys(namespace)
      .filter(name => typeof namespace[name] === "function" && !/Error$/.test(name))
      .map(name => `${module}#${name}`));
  assert.ok(callables.length >= 14, `only ${callables.length} callables found`);
  for (const callable of callables) {
    assert.ok(FUNCTIONS_CALLED.has(callable), `${callable} returned nothing for any shape`);
  }
  assert.ok(SURFACE.length > 200, `swept only ${SURFACE.length} values`);
});

test("the outcome-claim detector fires on each of the two claim shapes", () => {
  assert.deepEqual(outcomeClaims("verified", { entrance: { verified: true } }),
    [".entrance.verified === true"]);
  assert.deepEqual(outcomeClaims("read", { journal: { was_read: true } }), [".journal.was_read === true"]);
  assert.deepEqual(outcomeClaims("present", { admission: "present" }), ['.admission === "present"']);
  assert.deepEqual(outcomeClaims("ok", { deep: [{ status: "OK" }] }), ['.deep[0].status === "OK"']);
  assert.deepEqual(outcomeClaims("attended", [{ actor: { attended: true } }]), ["[0].actor.attended === true"]);
  // And it does not fire on naming a thing that is absent or false.
  assert.deepEqual(outcomeClaims("read", { reader_exists_here: false, reader_seam: "seam:x-reader" }), []);
  assert.deepEqual(outcomeClaims("attended", { actor_class: "human_attended" }), []);
  assert.deepEqual(outcomeClaims("equivalent", { governed_state_equivalent: false }), []);
  // The refusal vocabulary is skipped as a whole list, and only as that list.
  assert.deepEqual(outcomeClaims("verified", { fields: [...workflow.V5_J301_CALLER_AUTHORITY_FIELDS] }), []);
  assert.deepEqual(outcomeClaims("verified", { fields: ["verified"] }), ['.fields[0] === "verified"']);
  assert.ok(workflow.V5_J301_CALLER_AUTHORITY_FIELDS.includes("verified"),
    "the skip above would be vacuous if the vocabulary stopped naming the field");
});

for (const word of FORBIDDEN_OUTCOME_WORDS) {
  test(`no export of either module claims "${word}"`, () => {
    const hits = [];
    for (const { origin, name, value } of SURFACE) {
      if (value === true && wordTokens(name).includes(word)) hits.push(`${origin} === true`);
      for (const claim of outcomeClaims(word, value)) hits.push(`${origin}${claim}`);
    }
    assert.deepEqual(hits, [], `"${word}" claimed at: ${hits.slice(0, 6).join(" | ")}`);
  });
}

/** A structural copy with the declared refusal vocabularies emptied. */
function withoutRefusalVocabularies(value, depth = 0) {
  if (depth > 12 || !value || typeof value !== "object") return value;
  if (Array.isArray(value)) {
    if (isDeclaredRefusalVocabulary(value)) return [];
    return value.map(entry => withoutRefusalVocabularies(entry, depth + 1));
  }
  return Object.fromEntries(Object.entries(value)
    .map(([key, entry]) => [key, withoutRefusalVocabularies(entry, depth + 1)]));
}

test("the older privileged vocabulary holds over the same full enumeration", () => {
  // The narrower list items 1-5 kept, now run against every export rather than
  // the hand-typed callable subset it was written against. Same refusal-
  // vocabulary rule: `V5_J301_ASSIGNMENT_PHASES` names "committed" because that
  // is the Assignment phase a Tour may never set.
  assert.ok(workflow.V5_J301_ASSIGNMENT_PHASES.includes("committed"));
  for (const { origin, value } of SURFACE) {
    assert.equal(privilegedHit(withoutRefusalVocabularies(value)), null, origin);
  }
});
