import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createTourInternalWebHandler, isTourInternalRequest } from "../src/tour-internal-web.js";

// Slice 2 (create a Tour and assemble its stops). Synthetic identifiers only.
const ORIGIN = "https://app.doctorcre.com";
const ENV = { APP_HOST: "app.doctorcre.com" };
const ACTOR = { id: "partner-actor" };
const SESSION = { key: "opaque-server-session", csrfToken: "csrf-value" };
const id = n => `${String(n).repeat(8)}-${String(n).repeat(4)}-4${String(n).repeat(3)}-8${String(n).repeat(3)}-${String(n).repeat(12)}`;
const [tourId, baseRoute, draftRoute, propA, propB, propC, stopA, stopB, key] = [1, 2, 3, 4, 5, 6, 7, 8, 9].map(id);
const digest = `sha256:${"a".repeat(64)}`;
const start = { latitude: 30.5, longitude: -87.2, position_role: "start", precision_class: "approximate", source_ref: "fixture:start" };
const end = { ...start, position_role: "end", source_ref: "fixture:end" };
const headers = { origin: ORIGIN, "sec-fetch-site": "same-origin", "content-type": "application/json", "x-carr-csrf": SESSION.csrfToken };

const createBody = { idempotency_key: key, tour_name: "Synthetic Tour", subject_type: "client", subject_id: "client:fixture-1", canonical_dataset_version: "fixture-v1", start_point: start, end_point: end };
const draftBody = { idempotency_key: key, tour_id: tourId, route_version: 2, base_route_version_id: baseRoute, expected_route_version: 1, start_point: start, end_point: end };
const stopBody = { idempotency_key: key, route_version_id: draftRoute, property_id: propA, route_sequence: 1, route_label: "Stop 1", stop_state: "active",
  appointment_start: "2026-10-05T14:00:00.000Z", appointment_end: "2026-10-05T14:45:00.000Z", locked_appointment: true, dwell_minutes: 45, buffer_minutes: 15,
  access_coordinate_status: "approved", assertion_set_digest: digest };
const transitionBody = { idempotency_key: key, old_route_version_id: baseRoute, new_route_version_id: draftRoute, old_route_stop_id: stopA, new_route_stop_id: stopB, disposition: "reordered" };

const ROUTES = [
  ["/api/tours/create", "createTourFn", createBody],
  ["/api/tours/route-draft", "openRouteDraftFn", draftBody],
  ["/api/tours/route-stop", "appendRouteStopFn", stopBody],
  ["/api/tours/route-stop-transition", "appendRouteStopTransitionFn", transitionBody],
];
function post(path, body) { return new Request(`${ORIGIN}${path}`, { method: "POST", headers, body: JSON.stringify(body) }); }
function surface(overrides = {}) {
  const ok = async () => ({ ok: true, data: { saved: true } });
  return createTourInternalWebHandler({ ...Object.fromEntries(ROUTES.map(([, seam]) => [seam, ok])), ...overrides });
}
const fetchWith = (s, req, actor = ACTOR, session = SESSION) => s.fetch(req, ENV, {}, actor, session);

for (const [path, seam, body] of ROUTES) {
  test(`${path}: authenticated, CSRF-bound, exact-shape, and passes the actor untouched`, async () => {
    const seen = [];
    const s = surface({ [seam]: async context => { seen.push(context); return { ok: true, data: { saved: true } }; } });
    assert.equal(isTourInternalRequest(post(path, body)), true);
    assert.equal((await s.fetch(post(path, body), ENV, {}, undefined, undefined)).status, 401);
    const noCsrf = new Request(`${ORIGIN}${path}`, { method: "POST", headers: { ...headers, "x-carr-csrf": "wrong" }, body: JSON.stringify(body) });
    assert.equal((await fetchWith(s, noCsrf)).status, 403);
    assert.equal((await fetchWith(s, new Request(`${ORIGIN}${path}`, { method: "GET" }))).status, 405);
    assert.equal(seen.length, 0, "refused requests never reach the seam");
    assert.equal((await fetchWith(s, post(path, body))).status, 200);
    assert.deepEqual(seen[0].input, body);
    assert.deepEqual(seen[0].actor, ACTOR);
    for (const bad of [{ ...body, tenant_id: "other" }, { ...body, actor: "x" }, { ...body, extra: 1 }, { ...body, idempotency_key: "nope" }]) {
      assert.equal((await fetchWith(s, post(path, bad))).status, 400, JSON.stringify(Object.keys(bad)));
    }
    assert.equal(seen.length, 1);
  });
}

test("create refuses a bad subject type, a missing point, a wrong point role and a phase or deal field", async () => {
  const s = surface();
  for (const bad of [
    { ...createBody, subject_type: "deal" }, { ...createBody, subject_id: "has space" }, { ...createBody, tour_name: "  " },
    { ...createBody, start_point: null }, { ...createBody, start_point: end }, { ...createBody, end_point: { ...end, latitude: 91 } },
    { ...createBody, deal_phase: "touring" }, { ...createBody, assignment_id: id(5) },
  ]) assert.equal((await fetchWith(s, post("/api/tours/create", bad))).status, 400, JSON.stringify(bad).slice(0, 80));
});

test("stop edit keeps property identity apart from the route label and enforces state rules", async () => {
  const s = surface();
  const held = { ...stopBody, stop_state: "held", route_sequence: null, route_label: null, locked_appointment: false, appointment_start: null, appointment_end: null };
  const excluded = { ...held, stop_state: "excluded" };
  for (const okBody of [stopBody, held, excluded]) assert.equal((await fetchWith(s, post("/api/tours/route-stop", okBody))).status, 200);
  for (const bad of [
    { ...stopBody, route_label: null }, { ...stopBody, route_sequence: null }, { ...stopBody, route_label: propA + "!" },
    { ...held, route_sequence: 2 }, { ...held, route_label: "Stop 2" },
    { ...stopBody, property_id: "Stop 1" }, { ...stopBody, dwell_minutes: -1 }, { ...stopBody, buffer_minutes: 1441 },
    { ...stopBody, appointment_end: null }, { ...stopBody, appointment_end: "2026-10-05T13:00:00.000Z" },
    { ...stopBody, appointment_start: null, appointment_end: null, locked_appointment: true },
    { ...stopBody, locked_appointment: "yes" }, { ...stopBody, stop_state: "hidden" }, { ...stopBody, access_coordinate_status: "guess" },
    { ...stopBody, assertion_set_digest: "sha256:short" },
  ]) assert.equal((await fetchWith(s, post("/api/tours/route-stop", bad))).status, 400, JSON.stringify(bad).slice(0, 120));
});

test("stop transitions must name every changed order or exclusion with a legal disposition shape", async () => {
  const s = surface();
  const good = [
    transitionBody,
    { ...transitionBody, disposition: "added", old_route_version_id: null, old_route_stop_id: null },
    { ...transitionBody, disposition: "removed", new_route_stop_id: null },
    { ...transitionBody, disposition: "excluded", new_route_stop_id: stopB },
    { ...transitionBody, disposition: "held", new_route_stop_id: null },
  ];
  for (const b of good) assert.equal((await fetchWith(s, post("/api/tours/route-stop-transition", b))).status, 200, b.disposition);
  for (const bad of [
    { ...transitionBody, disposition: "added" }, { ...transitionBody, disposition: "removed" }, { ...transitionBody, disposition: "unchanged", new_route_stop_id: null },
    { ...transitionBody, disposition: "teleported" }, { ...transitionBody, disposition: "reordered", old_route_stop_id: null },
  ]) assert.equal((await fetchWith(s, post("/api/tours/route-stop-transition", bad))).status, 400, bad.disposition);
});

test("stale versions and accepted-version edits refuse as 409; unknown outcomes stay coarse 503 with no leak", async () => {
  const reports = [];
  const cases = [
    ["/api/tours/route-draft", "openRouteDraftFn", draftBody, "route version refuses concurrent or stale route state", 409],
    ["/api/tours/route-stop", "appendRouteStopFn", stopBody, "route stop cannot alter an accepted route version", 409],
    ["/api/tours/route-stop-transition", "appendRouteStopTransitionFn", transitionBody, "route stop cannot alter an accepted route version", 409],
    ["/api/tours/route-stop", "appendRouteStopFn", stopBody, "connect ECONNRESET db.internal.example:5432", 503],
  ];
  for (const [path, seam, body, message, status] of cases) {
    const s = createTourInternalWebHandler({ [seam]: async () => { throw new Error(message); }, reportFailureFn: r => reports.push(r) });
    const response = await fetchWith(s, post(path, body));
    assert.equal(response.status, status, message);
    assert.doesNotMatch(await response.text(), /ECONNRESET|db\.internal|accepted route/);
  }
  assert.ok(reports.every(r => !JSON.stringify(r).includes("db.internal")));
});

// A small in-memory domain that behaves like the append-only verbs: immutable
// versions, idempotent replay by key, a compare-and-swap on the accepted
// version, and no writes to an accepted version. It stands in for the database
// only so the browser-facing flow (create, edit, reload, stale, unknown
// outcome) can run offline; the verbs themselves are covered by the SQL tests.
function fakeDomain() {
  const db = { tours: new Map(), versions: new Map(), stops: new Map(), transitions: [], replays: new Map(), accepted: new Map(), phase: "none" };
  const once = (input, run) => {
    if (db.replays.has(input.idempotency_key)) return db.replays.get(input.idempotency_key);
    const result = { ok: true, data: run() }; db.replays.set(input.idempotency_key, result); return result;
  };
  const conflict = message => { throw new Error(message); };
  const seams = {
    createTourFn: async ({ input }) => once(input, () => { const t = id(1); db.tours.set(t, { id: t, subject_type: input.subject_type, subject_id: input.subject_id, status: "draft" }); return { tour_id: t }; }),
    openRouteDraftFn: async ({ input }) => once(input, () => {
      const latest = [...db.versions.values()].filter(v => v.tour_id === input.tour_id).length;
      if (input.expected_route_version !== (db.accepted.get(input.tour_id) ?? 0) || input.route_version !== latest + 1) conflict("route version refuses concurrent or stale route state");
      const v = id(3); db.versions.set(v, { id: v, tour_id: input.tour_id, route_version: input.route_version }); return { route_version_id: v };
    }),
    appendRouteStopFn: async ({ input }) => once(input, () => {
      if (db.accepted.has(input.route_version_id)) conflict("route stop cannot alter an accepted route version");
      const s = id(7 + db.stops.size); db.stops.set(s, { id: s, ...input }); return { route_stop_id: s };
    }),
    appendRouteStopTransitionFn: async ({ input }) => once(input, () => { db.transitions.push(input); return { route_stop_transition_id: id(5) }; }),
    acceptRouteVersionFn: async ({ input }) => once(input, () => { db.accepted.set(input.route_version_id, true); db.accepted.set(tourId, 1); return { accepted: true }; }),
    readTourFn: async () => ({ ok: true, data: { stops: [...db.stops.values()].map(({ id: stopId, property_id, route_label, stop_state, route_sequence, dwell_minutes, buffer_minutes, locked_appointment }) => ({ id: stopId, property_id, route_label, stop_state, route_sequence, dwell_minutes, buffer_minutes, locked_appointment })), deal_phase: db.phase } }),
  };
  return { db, seams };
}
const json = async response => (await response.json()).data;

test("create, edit, reload: held and excluded stops persist, order and property identity stay separate, no phase change", async () => {
  const { db, seams } = fakeDomain();
  const s = createTourInternalWebHandler(seams);
  const k = n => id(n);
  assert.equal((await json(await fetchWith(s, post("/api/tours/create", { ...createBody, idempotency_key: k(1) })))).tour_id, tourId);
  assert.equal((await json(await fetchWith(s, post("/api/tours/route-draft", { ...draftBody, route_version: 1, expected_route_version: 0, idempotency_key: k(2) })))).route_version_id, draftRoute);
  const rows = [
    { ...stopBody, idempotency_key: k(3), property_id: propA, route_sequence: 1, route_label: "First", dwell_minutes: 30 },
    { ...stopBody, idempotency_key: k(4), property_id: propB, stop_state: "held", route_sequence: null, route_label: null, locked_appointment: false, appointment_start: null, appointment_end: null },
    { ...stopBody, idempotency_key: k(5), property_id: propC, stop_state: "excluded", route_sequence: null, route_label: null, locked_appointment: false, appointment_start: null, appointment_end: null },
  ];
  for (const r of rows) assert.equal((await fetchWith(s, post("/api/tours/route-stop", r))).status, 200);
  const reloaded = await json(await fetchWith(s, new Request(`${ORIGIN}/api/tours/detail?tour_id=${tourId}`)));
  assert.deepEqual(reloaded.stops.map(x => [x.property_id, x.stop_state, x.route_label]), [[propA, "active", "First"], [propB, "held", null], [propC, "excluded", null]]);
  assert.ok(reloaded.stops.every(x => x.property_id !== x.route_label), "property ID is never the route label");
  assert.equal(reloaded.stops[0].dwell_minutes, 30);
  assert.equal(reloaded.deal_phase, "none", "assembly never touches an Assignment or Deal phase");
  assert.equal(db.transitions.length, 0);
});

test("stale draft is refused, and editing after acceptance is refused", async () => {
  const { seams } = fakeDomain();
  const s = createTourInternalWebHandler(seams);
  await fetchWith(s, post("/api/tours/route-draft", { ...draftBody, route_version: 1, expected_route_version: 0, idempotency_key: id(2) }));
  const stale = await fetchWith(s, post("/api/tours/route-draft", { ...draftBody, route_version: 1, expected_route_version: 0, idempotency_key: id(6) }));
  assert.equal(stale.status, 409);
  await fetchWith(s, post("/api/tours/route-accept", { route_version_id: draftRoute, expected_prior_route_version: 0, acceptance_digest: digest, idempotency_key: id(4) }));
  const after = await fetchWith(s, post("/api/tours/route-stop", { ...stopBody, idempotency_key: id(5) }));
  assert.equal(after.status, 409);
});

test("unknown outcome reconciles by replaying the same idempotency key without a duplicate stop", async () => {
  const { db, seams } = fakeDomain();
  await seams.createTourFn({ input: { ...createBody, idempotency_key: id(1) } });
  await seams.openRouteDraftFn({ input: { ...draftBody, route_version: 1, expected_route_version: 0, idempotency_key: id(2) } });
  // First attempt commits but the response is lost (transport drop): the browser
  // saw nothing. Replay with the same key must return the original result.
  let dropped = false;
  const s = createTourInternalWebHandler({ ...seams, appendRouteStopFn: async context => { const r = await seams.appendRouteStopFn(context); if (!dropped) { dropped = true; throw new Error("socket hang up"); } return r; } });
  const first = await fetchWith(s, post("/api/tours/route-stop", stopBody));
  assert.equal(first.status, 503, "browser cannot tell whether it committed");
  const detail = await json(await fetchWith(s, new Request(`${ORIGIN}/api/tours/detail?tour_id=${tourId}`)));
  assert.equal(detail.stops.length, 1, "reload shows the write did commit");
  const replay = await fetchWith(s, post("/api/tours/route-stop", stopBody));
  assert.equal(replay.status, 200);
  assert.equal(db.stops.size, 1, "same key never appends a second stop");
});

test("runtime maps each route to the existing verb and adds no Assignment, Deal or route-store write", async () => {
  const runtime = await readFile(new URL("../src/tour-runtime.js", import.meta.url), "utf8");
  assert.match(runtime, /createTourFn: context => invoke\(context, "create-tour-domain"/);
  assert.match(runtime, /openRouteDraftFn: context => invoke\(context, "append-tour-route-version"/);
  assert.match(runtime, /appendRouteStopFn: context => invoke\(context, "append-tour-route-stop"/);
  assert.match(runtime, /appendRouteStopTransitionFn: context => invoke\(context, "append-tour-route-stop-transition"/);
  assert.match(runtime, /routing_source: "manual"/);
  assert.doesNotMatch(runtime, /assignment|deal_phase|advance-deal/i);
});
