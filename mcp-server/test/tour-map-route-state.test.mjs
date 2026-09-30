import assert from "node:assert/strict";
import test from "node:test";
import {
  MAP_MODES, MAP_EVENT_TYPES, classifyPin, buildRouteVersionState, projectRoute,
  checkParity, reduceMapEvent, applyRouteVersion, cameraPlan, buildNativeNavLink,
  buildReturnState, resolveReturn, renderOrderedListHtml,
} from "../src/tour-map-route-state.js";

// Synthetic fixtures only: no client names, no real addresses.
const approvedPin = (lat, lng, role = "entrance") => ({
  latitude: lat, longitude: lng, coordinate_role: role, precision_class: "entrance",
  review_state: "reviewed", human_approved: true,
});
const centroidPin = (lat, lng) => ({
  latitude: lat, longitude: lng, coordinate_role: "parcel_centroid", precision_class: "parcel",
  review_state: "reviewed", human_approved: true,
});
const geocoderPin = (lat, lng) => ({
  latitude: lat, longitude: lng, coordinate_role: "geocoder_candidate", precision_class: "approximate",
  review_state: "unreviewed", human_approved: false,
});

function route(overrides = {}) {
  return {
    tour_id: "tour-synthetic-1",
    route_version: 3,
    stops: [
      { route_stop_id: "rs-a", property_id: "prop-a", route_sequence: 1, route_label: "A", locked_state: "locked",
        dwell_minutes: 30, buffer_minutes: 10, title: "Synthetic Clinic One", address_line: "100 Example Way",
        position: approvedPin(30.6001, -88.0001) },
      { route_stop_id: "rs-b", property_id: "prop-b", route_sequence: 2, route_label: "B", locked_state: "flexible",
        dwell_minutes: 20, buffer_minutes: 10, title: "Synthetic Clinic Two", address_line: "200 Example Way",
        position: centroidPin(30.6102, -88.0102) },
      { route_stop_id: "rs-c", property_id: "prop-c", route_sequence: 3, route_label: "C", locked_state: "flexible",
        dwell_minutes: 20, buffer_minutes: 10, title: "Synthetic Clinic Three", address_line: "300 Example Way",
        position: approvedPin(30.6203, -88.0203, "parking_access") },
    ],
    ...overrides,
  };
}
const approvedReceipt = { decision: "approved", route_version: 3, promotion_receipt_id: "rcpt-1" };

test("registers exactly the two modes and the seven typed events plus mode_change", () => {
  assert.deepEqual([...MAP_MODES], ["search", "tour"]);
  assert.deepEqual([...MAP_EVENT_TYPES].sort(), [
    "bounds_change", "draw_result", "feature_click", "filter_state", "mode_change",
    "route_stop_change", "selected_record", "slider_state",
  ]);
});

test("only entrance, driveway or parking access reviewed by a human is navigable", () => {
  assert.equal(classifyPin(approvedPin(1, 2)).navigable, true);
  assert.equal(classifyPin(approvedPin(1, 2, "driveway")).navigable, true);
  assert.equal(classifyPin(approvedPin(1, 2, "parking_access")).navigable, true);
  for (const pin of [centroidPin(1, 2), geocoderPin(1, 2)]) {
    const result = classifyPin(pin);
    assert.equal(result.navigable, false);
    assert.equal(result.display, "downgraded");
    assert.ok(result.reason.length > 0);
  }
  assert.equal(classifyPin({ ...approvedPin(1, 2), human_approved: false }).navigable, false);
  assert.equal(classifyPin({ ...approvedPin(1, 2), review_state: "unreviewed" }).navigable, false);
  assert.equal(classifyPin({ ...approvedPin(1, 2), review_state: "rejected" }).navigable, false);
  assert.equal(classifyPin({ ...approvedPin(1, 2), precision_class: "approximate" }).navigable, false);
  const missing = classifyPin(null);
  assert.equal(missing.navigable, false);
  assert.equal(missing.display, "unknown");
});

test("marker, list, card, story section and offline order all derive from one route version", () => {
  const state = buildRouteVersionState(route(), { mode: "tour", selected_property_id: "prop-b" });
  const projection = projectRoute(state);
  const ids = ["rs-a", "rs-b", "rs-c"];
  assert.deepEqual(projection.markers.map(item => item.route_stop_id), ids);
  assert.deepEqual(projection.list.map(item => item.route_stop_id), ids);
  assert.deepEqual(projection.story_sections.map(item => item.route_stop_id), ids);
  assert.deepEqual(projection.offline_itinerary.map(item => item.route_stop_id), ids);
  assert.deepEqual(projection.markers.map(item => item.label), ["A", "B", "C"]);
  assert.equal(projection.route_version, 3);
  assert.equal(projection.card.property_id, "prop-b");
  assert.equal(projection.card.route_stop_id, "rs-b");
  assert.equal(checkParity(projection).ok, true);
});

test("markers carry a text label and shape so meaning never rests on colour", () => {
  const projection = projectRoute(buildRouteVersionState(route(), { mode: "tour" }));
  for (const marker of projection.markers) {
    assert.ok(marker.accessible_name.includes(marker.label));
    assert.ok(["circle", "diamond"].includes(marker.shape));
  }
  assert.equal(projection.markers[0].shape, "circle");
  assert.equal(projection.markers[1].shape, "diamond");
});

test("centroid and unverified pins are visibly downgraded and withhold native navigation", () => {
  const projection = projectRoute(buildRouteVersionState(route(), { mode: "tour" }));
  const [a, b, c] = projection.markers;
  assert.equal(a.display, "verified");
  assert.equal(b.display, "downgraded");
  assert.match(b.precision_label, /approximate|centroid/i);
  assert.equal(c.display, "verified");
  const nav = Object.fromEntries(projection.list.map(item => [item.route_stop_id, item.native_navigation]));
  assert.equal(nav["rs-b"].available, false);
  assert.match(nav["rs-b"].reason, /entrance|driveway|parking/i);
  assert.equal(projection.offline_itinerary[1].coordinate_card, null);
  assert.ok(projection.offline_itinerary[1].address_line);
  assert.deepEqual(projection.exclusions.map(item => item.route_stop_id), ["rs-b"]);
});

test("the projection is deep-frozen so a surface cannot mutate parity", () => {
  const projection = projectRoute(buildRouteVersionState(route(), { mode: "search" }));
  assert.throws(() => { "use strict"; projection.markers.push({}); });
  assert.throws(() => { "use strict"; projection.markers[0].label = "Z"; });
});

test("parity check names the surface that disagrees", () => {
  const projection = structuredClone(projectRoute(buildRouteVersionState(route(), { mode: "tour" })));
  const broken = { ...projection, list: [projection.list[1], projection.list[0], projection.list[2]] };
  const result = checkParity(broken);
  assert.equal(result.ok, false);
  assert.ok(result.divergences.some(item => item.surface === "list"));
  const wrongLabel = { ...projection, markers: projection.markers.map((m, i) => (i === 0 ? { ...m, label: "Z" } : m)) };
  assert.equal(checkParity(wrongLabel).ok, false);
});

test("duplicate sequences or property identities are refused", () => {
  const dupSeq = route();
  dupSeq.stops[1].route_sequence = 1;
  assert.throws(() => buildRouteVersionState(dupSeq), /route_sequence/);
  const dupProp = route();
  dupProp.stops[1].property_id = "prop-a";
  assert.throws(() => buildRouteVersionState(dupProp), /property_id/);
  assert.throws(() => buildRouteVersionState(route({ route_version: 0 })), /route_version/);
});

test("route_sequence and label are presentation only: reorder keeps property identity", () => {
  const state = buildRouteVersionState(route(), { mode: "tour", selected_property_id: "prop-c" });
  const next = route({ route_version: 4 });
  next.stops = [next.stops[2], next.stops[0], next.stops[1]].map((stop, index) => ({
    ...stop, route_sequence: index + 1, route_label: "ABC"[index],
  }));
  const { state: reordered, mapping } = applyRouteVersion(state, next);
  assert.equal(reordered.selected_property_id, "prop-c");
  assert.deepEqual(projectRoute(reordered).markers.map(m => m.property_id), ["prop-c", "prop-a", "prop-b"]);
  assert.equal(mapping.length, 3);
  const c = mapping.find(item => item.property_id === "prop-c");
  assert.equal(c.old_route_sequence, 3);
  assert.equal(c.new_route_sequence, 1);
  assert.equal(c.old_route_label, "C");
  assert.equal(c.new_route_label, "A");
  assert.equal(c.disposition, "resequenced");
  assert.equal(c.old_route_version, 3);
  assert.equal(c.new_route_version, 4);
});

test("version mapping reports removed, added and unchanged stops explicitly", () => {
  const state = buildRouteVersionState(route(), { mode: "tour" });
  const next = route({ route_version: 4 });
  next.stops = [next.stops[0], { ...next.stops[2], route_sequence: 2, route_label: "B" }];
  next.stops.push({
    route_stop_id: "rs-d", property_id: "prop-d", route_sequence: 3, route_label: "C", locked_state: "flexible",
    dwell_minutes: 15, buffer_minutes: 5, title: "Synthetic Clinic Four", address_line: "400 Example Way",
    position: approvedPin(30.63, -88.03),
  });
  const { mapping } = applyRouteVersion(state, next);
  const byProperty = Object.fromEntries(mapping.map(item => [item.property_id, item.disposition]));
  assert.deepEqual(byProperty, { "prop-a": "unchanged", "prop-b": "removed", "prop-c": "resequenced", "prop-d": "added" });
});

test("a removed selection clears selection instead of pointing at a stale stop", () => {
  const state = buildRouteVersionState(route(), { mode: "tour", selected_property_id: "prop-b" });
  const next = route({ route_version: 4 });
  next.stops = [next.stops[0], next.stops[2]].map((stop, index) => ({ ...stop, route_sequence: index + 1, route_label: "AB"[index] }));
  const { state: after } = applyRouteVersion(state, next);
  assert.equal(after.selected_property_id, null);
});

test("typed events never remount the map and keep camera and selection", () => {
  let state = buildRouteVersionState(route(), { mode: "search" });
  const steps = [
    { type: "bounds_change", route_version: 3, bounds: [-88.1, 30.5, -87.9, 30.7] },
    { type: "feature_click", route_version: 3, property_id: "prop-a" },
    { type: "filter_state", route_version: 3, filters: { locked_state: "locked" } },
    { type: "slider_state", route_version: 3, name: "horizon_days", value: 7 },
    { type: "draw_result", route_version: 3, geometry: { type: "Polygon", coordinates: [[[0, 0], [0, 1], [1, 1], [0, 0]]] } },
  ];
  for (const event of steps) {
    const result = reduceMapEvent(state, event);
    assert.equal(result.effects.remount, false, event.type);
    assert.equal(result.effects.map_instance, "keep", event.type);
    state = result.state;
  }
  assert.deepEqual(state.camera.bounds, [-88.1, 30.5, -87.9, 30.7]);
  assert.equal(state.selected_property_id, "prop-a");
  assert.equal(state.filters.locked_state, "locked");
  assert.equal(state.sliders.horizon_days, 7);
  assert.ok(state.drawn_geometry);
});

test("switching Search to Tour and back keeps map instance, camera and selection", () => {
  let state = buildRouteVersionState(route(), { mode: "search", selected_property_id: "prop-c" });
  state = reduceMapEvent(state, { type: "bounds_change", route_version: 3, bounds: [-88.1, 30.5, -87.9, 30.7] }).state;
  const toTour = reduceMapEvent(state, { type: "mode_change", route_version: 3, mode: "tour" });
  assert.equal(toTour.state.mode, "tour");
  assert.equal(toTour.effects.remount, false);
  assert.equal(toTour.state.selected_property_id, "prop-c");
  assert.deepEqual(toTour.state.camera.bounds, [-88.1, 30.5, -87.9, 30.7]);
  const back = reduceMapEvent(toTour.state, { type: "mode_change", route_version: 3, mode: "search" });
  assert.equal(back.state.mode, "search");
  assert.equal(back.effects.remount, false);
});

test("route_stop_change moves progress and selection together in Tour mode", () => {
  const state = buildRouteVersionState(route(), { mode: "tour" });
  const { state: moved } = reduceMapEvent(state, { type: "route_stop_change", route_version: 3, route_stop_id: "rs-b" });
  assert.equal(moved.current_route_stop_id, "rs-b");
  assert.equal(moved.selected_property_id, "prop-b");
  const projection = projectRoute(moved);
  assert.equal(projection.card.route_stop_id, "rs-b");
  assert.equal(projection.story_sections.find(item => item.active).route_stop_id, "rs-b");
  assert.equal(projection.list.find(item => item.current).route_stop_id, "rs-b");
  assert.equal(projection.markers.find(item => item.current).route_stop_id, "rs-b");
});

test("events for an old route version or unknown targets are rejected without changing state", () => {
  const state = buildRouteVersionState(route(), { mode: "tour" });
  assert.throws(() => reduceMapEvent(state, { type: "feature_click", route_version: 2, property_id: "prop-a" }), /stale_route_version/);
  assert.throws(() => reduceMapEvent(state, { type: "route_stop_change", route_version: 3, route_stop_id: "rs-zzz" }), /unknown_route_stop/);
  assert.throws(() => reduceMapEvent(state, { type: "teleport", route_version: 3 }), /unknown_event/);
  assert.throws(() => reduceMapEvent(state, { type: "mode_change", route_version: 3, mode: "satellite" }), /unknown_mode/);
  assert.throws(() => reduceMapEvent(state, { type: "bounds_change", route_version: 3, bounds: [200, 0, 0, 0] }), /bounds/);
  assert.equal(state.current_route_stop_id, null);
});

test("route_stop_change is refused in Search mode", () => {
  const state = buildRouteVersionState(route(), { mode: "search" });
  assert.throws(() => reduceMapEvent(state, { type: "route_stop_change", route_version: 3, route_stop_id: "rs-a" }), /tour_mode_required/);
});

test("reduced motion removes camera animation", () => {
  assert.deepEqual(cameraPlan({ prefersReducedMotion: true }), { animate: false, duration_ms: 0, method: "jumpTo" });
  const normal = cameraPlan({ prefersReducedMotion: false });
  assert.equal(normal.animate, true);
  assert.equal(normal.method, "flyTo");
  assert.ok(normal.duration_ms > 0);
  const projection = projectRoute(buildRouteVersionState(route(), { mode: "tour" }), { prefersReducedMotion: true });
  assert.equal(projection.camera_plan.animate, false);
});

test("native link is withheld for a downgraded stop and carries the reason and a fallback", () => {
  const state = buildRouteVersionState(route(), { mode: "tour" });
  const result = buildNativeNavLink(state, {
    route_stop_id: "rs-b", platform: "apple_maps", travel_mode: "driving",
    promotion_receipt: approvedReceipt, now: "2026-09-30T12:00:00Z",
  });
  assert.equal(result.available, false);
  assert.equal(result.reason_code, "pin_not_entrance_approved");
  assert.equal(result.link, undefined);
  assert.ok(result.fallback.address_line);
  assert.equal(result.fallback.coordinate_card, null);
});

test("native link is withheld without an approved promotion receipt for this route version", () => {
  const state = buildRouteVersionState(route(), { mode: "tour" });
  const base = { route_stop_id: "rs-a", platform: "google_maps", travel_mode: "driving", now: "2026-09-30T12:00:00Z" };
  for (const receipt of [undefined, null, { ...approvedReceipt, decision: "rejected" }, { ...approvedReceipt, route_version: 2 }]) {
    const result = buildNativeNavLink(state, { ...base, promotion_receipt: receipt });
    assert.equal(result.available, false);
    assert.equal(result.reason_code, "promotion_receipt_missing_or_stale");
  }
});

test("an approved entrance with a matching receipt yields a minimal-data link for each platform", () => {
  const state = buildRouteVersionState(route(), { mode: "tour" });
  const apple = buildNativeNavLink(state, {
    route_stop_id: "rs-a", platform: "apple_maps", travel_mode: "driving",
    promotion_receipt: approvedReceipt, now: "2026-09-30T12:00:00Z",
  });
  assert.equal(apple.available, true);
  assert.equal(apple.link, "https://maps.apple.com/?daddr=30.6001,-88.0001&dirflg=d");
  const google = buildNativeNavLink(state, {
    route_stop_id: "rs-c", platform: "google_maps", travel_mode: "walking",
    promotion_receipt: approvedReceipt, now: "2026-09-30T12:00:00Z",
  });
  assert.equal(google.link, "https://www.google.com/maps/dir/?api=1&destination=30.6203,-88.0203&travelmode=walking");
  for (const result of [apple, google]) {
    assert.doesNotMatch(result.link, /Synthetic|prop-|rs-|tour-synthetic|Example/);
    assert.equal(result.return_state.route_stop_id, result.route_stop_id);
    assert.equal(result.return_state.route_version, 3);
    assert.ok(Date.parse(result.return_state.expires_at) > Date.parse("2026-09-30T12:00:00Z"));
  }
});

test("bad platform, travel mode or stop is refused", () => {
  const state = buildRouteVersionState(route(), { mode: "tour" });
  const good = { route_stop_id: "rs-a", platform: "apple_maps", travel_mode: "driving", promotion_receipt: approvedReceipt, now: "2026-09-30T12:00:00Z" };
  assert.throws(() => buildNativeNavLink(state, { ...good, platform: "waze" }), /platform/);
  assert.throws(() => buildNativeNavLink(state, { ...good, travel_mode: "flying" }), /travel_mode/);
  assert.throws(() => buildNativeNavLink(state, { ...good, route_stop_id: "rs-nope" }), /unknown_route_stop/);
});

test("return after native navigation lands on the exact stop that was handed off", () => {
  const state = buildRouteVersionState(route(), { mode: "tour" });
  const handoff = buildNativeNavLink(state, {
    route_stop_id: "rs-c", platform: "apple_maps", travel_mode: "driving",
    promotion_receipt: approvedReceipt, now: "2026-09-30T12:00:00Z", user_ref: "user-synthetic",
  });
  const marker = buildReturnState(handoff);
  const back = resolveReturn(state, marker, { now: "2026-09-30T12:20:00Z", user_ref: "user-synthetic" });
  assert.equal(back.ok, true);
  assert.equal(back.route_stop_id, "rs-c");
  assert.equal(back.state.current_route_stop_id, "rs-c");
  assert.equal(back.state.selected_property_id, "prop-c");
  assert.equal(back.state.mode, "tour");
});

test("return follows the version mapping when the route was reordered while away", () => {
  const state = buildRouteVersionState(route(), { mode: "tour" });
  const handoff = buildNativeNavLink(state, {
    route_stop_id: "rs-c", platform: "apple_maps", travel_mode: "driving",
    promotion_receipt: approvedReceipt, now: "2026-09-30T12:00:00Z",
  });
  const next = route({ route_version: 4 });
  next.stops = [next.stops[2], next.stops[0], next.stops[1]].map((stop, index) => ({
    ...stop, route_stop_id: `${stop.route_stop_id}-v4`, route_sequence: index + 1, route_label: "ABC"[index],
  }));
  const { state: newer } = applyRouteVersion(state, next);
  const back = resolveReturn(newer, buildReturnState(handoff), { now: "2026-09-30T12:20:00Z" });
  assert.equal(back.ok, true);
  assert.equal(back.route_stop_id, "rs-c-v4");
  assert.equal(back.state.selected_property_id, "prop-c");
  assert.equal(back.note, "route_version_changed");
});

test("return degrades to the ordered list when the token is expired, foreign or the stop is gone", () => {
  const state = buildRouteVersionState(route(), { mode: "tour" });
  const handoff = buildNativeNavLink(state, {
    route_stop_id: "rs-a", platform: "google_maps", travel_mode: "driving",
    promotion_receipt: approvedReceipt, now: "2026-09-30T12:00:00Z", user_ref: "user-synthetic",
  });
  const marker = buildReturnState(handoff);
  const expired = resolveReturn(state, marker, { now: "2026-10-02T12:00:00Z", user_ref: "user-synthetic" });
  assert.equal(expired.ok, false);
  assert.equal(expired.reason_code, "return_expired");
  assert.equal(expired.fallback, "ordered_list");
  const foreign = resolveReturn(state, marker, { now: "2026-09-30T12:05:00Z", user_ref: "someone-else" });
  assert.equal(foreign.reason_code, "return_user_mismatch");
  const otherTour = resolveReturn({ ...state, tour_id: "tour-other" }, marker, { now: "2026-09-30T12:05:00Z", user_ref: "user-synthetic" });
  assert.equal(otherTour.reason_code, "return_tour_mismatch");
  const gone = route({ route_version: 4 });
  gone.stops = gone.stops.slice(1).map((stop, index) => ({ ...stop, route_sequence: index + 1, route_label: "AB"[index] }));
  const { state: withoutA } = applyRouteVersion(state, gone);
  const missing = resolveReturn(withoutA, marker, { now: "2026-09-30T12:05:00Z", user_ref: "user-synthetic" });
  assert.equal(missing.reason_code, "return_stop_removed");
  assert.equal(missing.fallback, "ordered_list");
});

test("ordered list fallback renders every stop in order, escapes text and needs no tiles", () => {
  const data = route();
  data.stops[0].title = "<img src=x onerror=alert(1)> Clinic";
  const projection = projectRoute(buildRouteVersionState(data, { mode: "tour" }));
  const html = renderOrderedListHtml(projection);
  assert.match(html, /^<ol /);
  assert.equal((html.match(/<li /g) || []).length, 3);
  assert.ok(html.indexOf("Synthetic Clinic Two") < html.indexOf("Synthetic Clinic Three"));
  assert.doesNotMatch(html, /<img/);
  assert.match(html, /&lt;img/);
  assert.match(html, /Approximate location/);
  assert.doesNotMatch(html, /https?:\/\/[^"]*tile/);
  assert.match(html, /aria-label="Tour stops in visit order"/);
});

test("the module has no imports, so the app can vendor it into a browser bundle", async () => {
  const { readFile } = await import("node:fs/promises");
  const source = await readFile(new URL("../src/tour-map-route-state.js", import.meta.url), "utf8");
  assert.doesNotMatch(source, /^\s*import\s/m);
  assert.doesNotMatch(source, /require\(|process\.|node:/);
});

test("the measurement script reports parity and withheld navigation for a route file", async () => {
  const { execFile } = await import("node:child_process");
  const { promisify } = await import("node:util");
  const fixture = new URL("./fixtures/tour-map-route-state.synthetic.json", import.meta.url).pathname;
  const script = new URL("../bin/measure-tour-map-route.mjs", import.meta.url).pathname;
  const { stdout } = await promisify(execFile)(process.execPath, [script, fixture]);
  const report = JSON.parse(stdout);
  assert.equal(report.parity_ok, true);
  assert.equal(report.stop_count, 2);
  assert.equal(report.downgraded_or_unknown, 1);
  assert.equal(report.stops[0].navigation, "promotion_receipt_missing_or_stale");
  assert.equal(report.stops[1].navigation, "pin_not_entrance_approved");
});
