// Tour map route-version state (V0 slice 3, DoctorCRE v5 / WR-000012).
//
// ONE canonical route version feeds the marker, the list row, the property card,
// the story section and the offline itinerary, so they cannot disagree about
// order. Pure and import-free on purpose: the tours app vendors this file into a
// browser bundle and the tests run it unchanged in Node.
//
// Doctrine (workspace/contracts/market-map-route-planning.v1.json, 1.2.0):
//   * property identity is separate from mutable route_sequence / route_label;
//   * Search and Tour modes switch through typed events, never a remount;
//   * only a human-approved entrance, driveway or parking-access position gets a
//     native navigation link; centroids, geocoder candidates and unreviewed pins
//     are visibly downgraded and their link is withheld;
//   * no navigation link without an approved promotion receipt for this route
//     version. The receipt is READ elsewhere (a retrieval adapter is still owed,
//     see V5_J301_PROMOTION_RECEIPT_RETRIEVAL_SEAM); this module only checks the
//     receipt object it is handed and never issues one;
//   * an ordered list stays usable with no tiles.
//
// Nothing here calls a provider, a verb or the network.

export const MAP_MODES = Object.freeze(["search", "tour"]);
export const MAP_EVENT_TYPES = Object.freeze([
  "feature_click", "bounds_change", "draw_result", "filter_state", "slider_state",
  "selected_record", "route_stop_change", "mode_change",
]);
export const NAV_PLATFORMS = Object.freeze(["apple_maps", "google_maps"]);
export const NAV_TRAVEL_MODES = Object.freeze(["driving", "walking"]);
const NAVIGABLE_ROLES = Object.freeze(["entrance", "driveway", "parking_access"]);
const NAVIGABLE_PRECISION = Object.freeze(["entrance", "surveyed"]);
const RETURN_TTL_MS = 12 * 60 * 60 * 1000;

export class RouteStateError extends Error {
  constructor(code, message, detail) {
    super(`${code}: ${message}`);
    this.name = "RouteStateError";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}
const fail = (code, message, detail) => { throw new RouteStateError(code, message, detail); };

function deepFreeze(value) {
  if (value && typeof value === "object" && !Object.isFrozen(value)) {
    Object.values(value).forEach(deepFreeze);
    Object.freeze(value);
  }
  return value;
}

function text(value, path) {
  if (typeof value !== "string" || !value.trim()) fail("invalid_shape", `${path} must be a non-empty string`);
  return value;
}

function finite(value, min, max, path) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < min || value > max) {
    fail("invalid_coordinate", `${path} must be a number between ${min} and ${max}`);
  }
  return value;
}

/** Decide how a pin may be shown and whether it may drive native navigation. */
export function classifyPin(position) {
  if (!position || typeof position !== "object") {
    return { navigable: false, display: "unknown", precision_label: "Location unknown",
      reason: "No coordinate on record. Add or approve an entrance, driveway or parking access point." };
  }
  const roleOk = NAVIGABLE_ROLES.includes(position.coordinate_role);
  const precisionOk = NAVIGABLE_PRECISION.includes(position.precision_class);
  const reviewed = position.review_state === "reviewed" && position.human_approved === true;
  if (roleOk && precisionOk && reviewed) {
    return { navigable: true, display: "verified", precision_label: "Approved access point", reason: null };
  }
  const role = String(position.coordinate_role || "unknown").replace(/_/g, " ");
  const why = !roleOk
    ? `The pin is a ${role}, not an entrance, driveway or parking access point.`
    : !reviewed ? "The access point has not been approved by a human reviewer."
      : "The pin precision is below entrance level.";
  return { navigable: false, display: "downgraded", precision_label: "Approximate location", reason: why };
}

function normalizeStop(stop, index) {
  const path = `stops[${index}]`;
  if (!stop || typeof stop !== "object") fail("invalid_shape", `${path} must be an object`);
  if (!Number.isSafeInteger(stop.route_sequence) || stop.route_sequence < 1) {
    fail("invalid_route_sequence", `${path}.route_sequence must be a positive integer`);
  }
  const position = stop.position ? {
    latitude: finite(stop.position.latitude, -90, 90, `${path}.position.latitude`),
    longitude: finite(stop.position.longitude, -180, 180, `${path}.position.longitude`),
    coordinate_role: stop.position.coordinate_role ?? null,
    precision_class: stop.position.precision_class ?? null,
    review_state: stop.position.review_state ?? null,
    human_approved: stop.position.human_approved === true,
  } : null;
  return {
    route_stop_id: text(stop.route_stop_id, `${path}.route_stop_id`),
    property_id: text(stop.property_id, `${path}.property_id`),
    route_sequence: stop.route_sequence,
    route_label: text(stop.route_label, `${path}.route_label`),
    locked_state: stop.locked_state === "locked" ? "locked" : "flexible",
    dwell_minutes: Number.isFinite(stop.dwell_minutes) ? stop.dwell_minutes : null,
    buffer_minutes: Number.isFinite(stop.buffer_minutes) ? stop.buffer_minutes : null,
    title: text(stop.title, `${path}.title`),
    address_line: typeof stop.address_line === "string" ? stop.address_line : "",
    position,
  };
}

/** Build the one canonical state. Everything else is derived from `state.route`. */
export function buildRouteVersionState(route, options = {}) {
  if (!route || typeof route !== "object") fail("invalid_shape", "route must be an object");
  if (!Number.isSafeInteger(route.route_version) || route.route_version < 1) {
    fail("invalid_route_version", "route_version must be a positive integer");
  }
  if (!Array.isArray(route.stops)) fail("invalid_shape", "route.stops must be an array");
  const stops = route.stops.map(normalizeStop).sort((a, b) => a.route_sequence - b.route_sequence);
  for (const field of ["route_sequence", "property_id", "route_stop_id"]) {
    if (new Set(stops.map(stop => stop[field])).size !== stops.length) {
      fail(`duplicate_${field}`, `${field} must be unique within one route version`);
    }
  }
  const mode = options.mode ?? "search";
  if (!MAP_MODES.includes(mode)) fail("unknown_mode", `"${mode}" is not a map mode`);
  const known = new Set(stops.map(stop => stop.property_id));
  const selected = options.selected_property_id ?? null;
  return {
    tour_id: text(route.tour_id, "tour_id"),
    route_version: route.route_version,
    route: { stops },
    mode,
    selected_property_id: selected !== null && known.has(selected) ? selected : null,
    current_route_stop_id: null,
    camera: { bounds: null },
    filters: {},
    sliders: {},
    drawn_geometry: null,
  };
}

function stopById(state, id) {
  const stop = state.route.stops.find(item => item.route_stop_id === id);
  if (!stop) fail("unknown_route_stop", `no stop "${id}" in route version ${state.route_version}`);
  return stop;
}

/** Reduced-motion aware camera plan. No essential explanation is animation-only. */
export function cameraPlan({ prefersReducedMotion = false } = {}) {
  return prefersReducedMotion
    ? { animate: false, duration_ms: 0, method: "jumpTo" }
    : { animate: true, duration_ms: 600, method: "flyTo" };
}

function navigationStatus(stop, pin) {
  return pin.navigable
    ? { available: true, reason: null }
    : { available: false, reason_code: "pin_not_entrance_approved", reason: pin.reason };
}

/** Derive every surface from the one route version. */
export function projectRoute(state, { prefersReducedMotion = false } = {}) {
  const selectedStop = state.route.stops.find(stop => stop.property_id === state.selected_property_id) ?? null;
  const rows = state.route.stops.map(stop => {
    const pin = classifyPin(stop.position);
    const current = stop.route_stop_id === state.current_route_stop_id;
    return { stop, pin, current };
  });
  const markers = rows.map(({ stop, pin, current }) => ({
    route_stop_id: stop.route_stop_id,
    property_id: stop.property_id,
    label: stop.route_label,
    route_sequence: stop.route_sequence,
    shape: stop.locked_state === "locked" ? "circle" : "diamond",
    display: pin.display,
    precision_label: pin.precision_label,
    position: stop.position && pin.display !== "unknown"
      ? { latitude: stop.position.latitude, longitude: stop.position.longitude } : null,
    selected: stop.property_id === state.selected_property_id,
    current,
    accessible_name: `Stop ${stop.route_label}, ${stop.title}, ${stop.locked_state} stop, ${pin.precision_label}`,
  }));
  const list = rows.map(({ stop, pin, current }) => ({
    route_stop_id: stop.route_stop_id,
    property_id: stop.property_id,
    label: stop.route_label,
    route_sequence: stop.route_sequence,
    title: stop.title,
    locked_state: stop.locked_state,
    display: pin.display,
    precision_label: pin.precision_label,
    selected: stop.property_id === state.selected_property_id,
    current,
    native_navigation: navigationStatus(stop, pin),
  }));
  const storySections = rows.map(({ stop, current }) => ({
    route_stop_id: stop.route_stop_id,
    label: stop.route_label,
    route_sequence: stop.route_sequence,
    title: stop.title,
    active: current,
  }));
  const offline = rows.map(({ stop, pin }) => ({
    route_stop_id: stop.route_stop_id,
    label: stop.route_label,
    route_sequence: stop.route_sequence,
    title: stop.title,
    address_line: stop.address_line,
    dwell_minutes: stop.dwell_minutes,
    buffer_minutes: stop.buffer_minutes,
    display: pin.display,
    coordinate_card: pin.navigable
      ? { latitude: stop.position.latitude, longitude: stop.position.longitude, basis: "approved_access_point" }
      : null,
  }));
  const card = selectedStop ? (() => {
    const pin = classifyPin(selectedStop.position);
    return {
      route_stop_id: selectedStop.route_stop_id,
      property_id: selectedStop.property_id,
      label: selectedStop.route_label,
      route_sequence: selectedStop.route_sequence,
      title: selectedStop.title,
      address_line: selectedStop.address_line,
      locked_state: selectedStop.locked_state,
      dwell_minutes: selectedStop.dwell_minutes,
      buffer_minutes: selectedStop.buffer_minutes,
      display: pin.display,
      precision_label: pin.precision_label,
      native_navigation: navigationStatus(selectedStop, pin),
    };
  })() : null;
  const next = rows.find(({ stop }) => state.current_route_stop_id === null
    ? false : stop.route_sequence > (stopById(state, state.current_route_stop_id).route_sequence));
  return deepFreeze({
    tour_id: state.tour_id,
    route_version: state.route_version,
    mode: state.mode,
    markers, list, card,
    story_sections: storySections,
    offline_itinerary: offline,
    next_stop_id: next ? next.stop.route_stop_id : null,
    exclusions: rows.filter(({ pin }) => !pin.navigable).map(({ stop, pin }) => ({
      route_stop_id: stop.route_stop_id, display: pin.display, reason: pin.reason,
    })),
    camera_plan: cameraPlan({ prefersReducedMotion }),
  });
}

/** Prove marker, list, story section and offline order agree; name any that do not. */
export function checkParity(projection) {
  const reference = projection.markers.map(item => `${item.route_sequence}:${item.route_stop_id}:${item.label}`);
  const surfaces = {
    list: projection.list, story_sections: projection.story_sections, offline_itinerary: projection.offline_itinerary,
  };
  const divergences = [];
  for (const [surface, items] of Object.entries(surfaces)) {
    const seen = items.map(item => `${item.route_sequence}:${item.route_stop_id}:${item.label}`);
    if (seen.length !== reference.length || seen.some((value, index) => value !== reference[index])) {
      divergences.push({ surface, expected: reference, actual: seen });
    }
  }
  const sequences = projection.markers.map(item => item.route_sequence);
  if (sequences.some((value, index) => index > 0 && value <= sequences[index - 1])) {
    divergences.push({ surface: "markers", expected: "strictly ascending route_sequence", actual: sequences });
  }
  return { ok: divergences.length === 0, divergences };
}

function assertBounds(bounds) {
  if (!Array.isArray(bounds) || bounds.length !== 4) fail("invalid_bounds", "bounds must be [west,south,east,north]");
  finite(bounds[0], -180, 180, "bounds.west"); finite(bounds[1], -90, 90, "bounds.south");
  finite(bounds[2], -180, 180, "bounds.east"); finite(bounds[3], -90, 90, "bounds.north");
}

/**
 * Typed events in, next state out. The effects record says the map instance is
 * kept: nothing here asks a caller to remount, rebuild or discard camera state.
 */
export function reduceMapEvent(state, event) {
  if (!event || typeof event !== "object" || !MAP_EVENT_TYPES.includes(event.type)) {
    fail("unknown_event", `"${event && event.type}" is not a registered map event`);
  }
  if (event.route_version !== state.route_version) {
    fail("stale_route_version", `event is for route version ${event.route_version}, state is ${state.route_version}`,
      { event_route_version: event.route_version, state_route_version: state.route_version });
  }
  const next = structuredClone(state);
  switch (event.type) {
    case "mode_change":
      if (!MAP_MODES.includes(event.mode)) fail("unknown_mode", `"${event.mode}" is not a map mode`);
      next.mode = event.mode;
      break;
    case "bounds_change":
      assertBounds(event.bounds);
      next.camera = { ...next.camera, bounds: [...event.bounds] };
      break;
    case "feature_click":
    case "selected_record": {
      const property = text(event.property_id, "property_id");
      if (!next.route.stops.some(stop => stop.property_id === property)) {
        fail("unknown_property", `property "${property}" is not on route version ${state.route_version}`);
      }
      next.selected_property_id = property;
      break;
    }
    case "filter_state":
      if (!event.filters || typeof event.filters !== "object" || Array.isArray(event.filters)) {
        fail("invalid_shape", "filters must be an object");
      }
      next.filters = { ...event.filters };
      break;
    case "slider_state":
      next.sliders = { ...next.sliders, [text(event.name, "name")]: event.value };
      break;
    case "draw_result":
      if (!event.geometry || typeof event.geometry !== "object") fail("invalid_shape", "geometry must be an object");
      next.drawn_geometry = structuredClone(event.geometry);
      break;
    case "route_stop_change": {
      if (state.mode !== "tour") fail("tour_mode_required", "route progress only moves in Tour mode");
      const stop = stopById(state, event.route_stop_id);
      next.current_route_stop_id = stop.route_stop_id;
      next.selected_property_id = stop.property_id;
      break;
    }
    default:
      break;
  }
  return { state: next, effects: { remount: false, map_instance: "keep" } };
}

/** Move to a new route version, keeping identity-keyed state and recording the mapping. */
export function applyRouteVersion(state, route) {
  const next = buildRouteVersionState(route, { mode: state.mode });
  if (next.tour_id !== state.tour_id) fail("tour_mismatch", "a route version cannot change tour");
  if (next.route_version <= state.route_version) {
    fail("stale_route_version", "the new route version must be greater than the current one");
  }
  const oldByProperty = new Map(state.route.stops.map(stop => [stop.property_id, stop]));
  const newByProperty = new Map(next.route.stops.map(stop => [stop.property_id, stop]));
  const mapping = [];
  for (const old of state.route.stops) {
    const now = newByProperty.get(old.property_id);
    mapping.push({
      old_route_version: state.route_version, new_route_version: next.route_version, property_id: old.property_id,
      old_route_stop_id: old.route_stop_id, new_route_stop_id: now ? now.route_stop_id : null,
      old_route_sequence: old.route_sequence, new_route_sequence: now ? now.route_sequence : null,
      old_route_label: old.route_label, new_route_label: now ? now.route_label : null,
      disposition: !now ? "removed"
        : now.route_sequence === old.route_sequence && now.route_label === old.route_label ? "unchanged" : "resequenced",
    });
  }
  for (const now of next.route.stops) {
    if (!oldByProperty.has(now.property_id)) {
      mapping.push({
        old_route_version: state.route_version, new_route_version: next.route_version, property_id: now.property_id,
        old_route_stop_id: null, new_route_stop_id: now.route_stop_id,
        old_route_sequence: null, new_route_sequence: now.route_sequence,
        old_route_label: null, new_route_label: now.route_label, disposition: "added",
      });
    }
  }
  const carried = {
    ...next,
    selected_property_id: newByProperty.has(state.selected_property_id) ? state.selected_property_id : null,
    current_route_stop_id: null,
    camera: structuredClone(state.camera),
    filters: structuredClone(state.filters),
    sliders: structuredClone(state.sliders),
    drawn_geometry: structuredClone(state.drawn_geometry),
    version_mapping: mapping,
  };
  const previousCurrent = state.current_route_stop_id
    ? state.route.stops.find(stop => stop.route_stop_id === state.current_route_stop_id) : null;
  if (previousCurrent && newByProperty.has(previousCurrent.property_id)) {
    carried.current_route_stop_id = newByProperty.get(previousCurrent.property_id).route_stop_id;
  }
  return { state: carried, mapping };
}

/** Build a native navigation link, or withhold it with a reason and an offline fallback. */
export function buildNativeNavLink(state, request) {
  const platform = request.platform;
  if (!NAV_PLATFORMS.includes(platform)) fail("unknown_platform", `"${platform}" is not a navigation platform`);
  const travelMode = request.travel_mode;
  if (!NAV_TRAVEL_MODES.includes(travelMode)) fail("unknown_travel_mode", `"${travelMode}" is not a travel mode`);
  const stop = stopById(state, request.route_stop_id);
  const pin = classifyPin(stop.position);
  const fallback = {
    route_stop_id: stop.route_stop_id, label: stop.route_label, address_line: stop.address_line,
    coordinate_card: pin.navigable
      ? { latitude: stop.position.latitude, longitude: stop.position.longitude, basis: "approved_access_point" } : null,
  };
  const base = { route_stop_id: stop.route_stop_id, route_version: state.route_version, fallback };
  if (!pin.navigable) {
    return { ...base, available: false, reason_code: "pin_not_entrance_approved", reason: pin.reason };
  }
  const receipt = request.promotion_receipt;
  if (!receipt || receipt.decision !== "approved" || receipt.route_version !== state.route_version) {
    return { ...base, available: false, reason_code: "promotion_receipt_missing_or_stale",
      reason: "No approved map promotion receipt covers this route version." };
  }
  const { latitude, longitude } = stop.position;
  const link = platform === "apple_maps"
    ? `https://maps.apple.com/?daddr=${latitude},${longitude}&dirflg=${travelMode === "walking" ? "w" : "d"}`
    : `https://www.google.com/maps/dir/?api=1&destination=${latitude},${longitude}&travelmode=${travelMode}`;
  const generated = Date.parse(request.now);
  if (!Number.isFinite(generated)) fail("invalid_time", "now must be an ISO timestamp");
  return {
    ...base, available: true, platform, travel_mode: travelMode, link,
    return_state: {
      tour_id: state.tour_id, route_stop_id: stop.route_stop_id, property_id: stop.property_id,
      route_version: state.route_version, user_ref: request.user_ref ?? null,
      generated_at: new Date(generated).toISOString(), expires_at: new Date(generated + RETURN_TTL_MS).toISOString(),
    },
  };
}

/** The marker the client persists before leaving for the native app. */
export function buildReturnState(handoff) {
  if (!handoff || handoff.available !== true || !handoff.return_state) {
    fail("no_handoff", "a return marker exists only for an available handoff");
  }
  return structuredClone(handoff.return_state);
}

/** On return, restore the exact stop, or say why not and fall back to the ordered list. */
export function resolveReturn(state, marker, { now, user_ref = null } = {}) {
  const refuse = (reason_code) => ({ ok: false, reason_code, fallback: "ordered_list" });
  if (marker.tour_id !== state.tour_id) return refuse("return_tour_mismatch");
  if (marker.user_ref !== null && marker.user_ref !== user_ref) return refuse("return_user_mismatch");
  if (!(Date.parse(now) < Date.parse(marker.expires_at))) return refuse("return_expired");
  let stop = null;
  let note = null;
  if (marker.route_version === state.route_version) {
    stop = state.route.stops.find(item => item.route_stop_id === marker.route_stop_id) ?? null;
  } else {
    stop = state.route.stops.find(item => item.property_id === marker.property_id) ?? null;
    note = "route_version_changed";
  }
  if (!stop) return refuse("return_stop_removed");
  return {
    ok: true, route_stop_id: stop.route_stop_id, note,
    state: { ...structuredClone(state), mode: "tour", current_route_stop_id: stop.route_stop_id, selected_property_id: stop.property_id },
  };
}

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ESCAPES[char]);

/** Tile-free ordered list. Usable when the map fails to load or tiles are offline. */
export function renderOrderedListHtml(projection) {
  const items = projection.list.map(item => {
    const offline = projection.offline_itinerary.find(entry => entry.route_stop_id === item.route_stop_id);
    const notice = item.display === "verified" ? "" : ` <span class="tour-pin-note">${esc(item.precision_label)}</span>`;
    const nav = item.native_navigation.available ? "" : ` <span class="tour-nav-withheld">Navigation not available: ${esc(item.native_navigation.reason)}</span>`;
    return `<li data-route-stop-id="${esc(item.route_stop_id)}"${item.current ? ' aria-current="step"' : ""}>`
      + `<strong>${esc(item.label)}</strong> ${esc(item.title)}`
      + (offline && offline.address_line ? `, ${esc(offline.address_line)}` : "") + notice + nav + "</li>";
  });
  return `<ol class="tour-stop-list" aria-label="Tour stops in visit order">${items.join("")}</ol>`;
}
