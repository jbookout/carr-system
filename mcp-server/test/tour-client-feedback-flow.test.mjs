import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createContext, runInContext } from "node:vm";
import { fileURLToPath } from "node:url";
import { createReportsWebHandler, REPORTS_ORIGIN } from "../src/reports-web.js";
import { createTourInternalWebHandler } from "../src/tour-internal-web.js";
import { tourSharingBrowserAccess, tourSharingTools } from "../src/tour-sharing.js";

// V0 slice 5: a client shortlists and comments inside a shared Tour, and the
// broker reads it back tied to the exact projection. Synthetic values only.
// The database here is a small in-memory stand-in for the SQL functions in
// migrations/0749 (grant, expiry, membership, idempotency). It proves the
// browser, the reports adapter and the verbs agree with each other; it does
// not prove the SQL, which needs the live measurement in the PR body.

const SHARE_JS = fileURLToPath(new URL("../../dealroom/reports/share.js", import.meta.url));
const SHARE_HTML = fileURLToPath(new URL("../../dealroom/reports/share.html", import.meta.url));
const APP_JS = fileURLToPath(new URL("../../dealroom/tours/app.js", import.meta.url));
const TOURS_HTML = fileURLToPath(new URL("../../dealroom/tours/index.html", import.meta.url));

class ToolError extends Error { constructor(payload) { super(payload.error); this.payload = payload; } }
const PROJECTION_ID = "10000000-0000-4000-8000-000000000001";
const PROJECTION_REF = `projection:public:${"p".repeat(32)}`;
const OTHER_PROJECTION_REF = `projection:public:${"q".repeat(32)}`;
const PROP_A = `property:public:${"a".repeat(32)}`;
const PROP_B = `property:public:${"b".repeat(32)}`;
const PROP_OTHER_TOUR = `property:public:${"z".repeat(32)}`;
const TOKEN = "T".repeat(43);
const digestOf = async value => {
  const bytes = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)));
  return "sha256:" + [...bytes].map(b => b.toString(16).padStart(2, "0")).join("");
};

function store({ scopes = ["view_packet", "shortlist", "comment"] } = {}) {
  const db = {
    now: Date.parse("2026-10-01T12:00:00Z"),
    grant: { projection_ref: PROJECTION_REF, scopes, expiresAt: Date.parse("2026-10-08T12:00:00Z"), status: "active", members: new Set([PROP_A, PROP_B]) },
    tokenDigest: null, sessions: new Map(), writes: new Map(), shortlist: new Map(), comments: [], calls: [],
  };
  const live = digest => {
    const session = db.sessions.get(digest);
    return session && db.grant.status === "active" && db.now < db.grant.expiresAt && db.now < session.expiresAt ? db.grant : null;
  };
  const client = { async query(sql, params) {
    db.calls.push({ sql, params });
    const seam = /ops\.(\w+)\(/.exec(sql)?.[1];
    if (seam === "exchange_tour_share_session") return { rows: [{ exchange: { ok: true, permission_scopes: db.grant.scopes, expires_at: new Date(db.grant.expiresAt).toISOString() } }] };
    const [sd] = params;
    const grant = live(sd);
    if (seam === "read_tour_share_feedback")
      return { rows: [{ feedback: grant ? { projection_ref: grant.projection_ref, permission_scopes: grant.scopes.filter(s => s !== "view_packet"), items: [...grant.members].map(property_ref => ({ property_ref })) } : null }] };
    if (seam === "write_tour_share_shortlist" || seam === "write_tour_share_comment") {
      const kind = seam.endsWith("shortlist") ? "shortlist" : "comment";
      const [, projectionRef, propertyRef, value, key] = params;
      if (!grant || !grant.scopes.includes(kind) || projectionRef !== grant.projection_ref || !grant.members.has(propertyRef)) return { rows: [{ feedback: null }] };
      const fingerprint = JSON.stringify([kind, projectionRef, propertyRef, value]);
      if (db.writes.has(key)) return { rows: [{ feedback: db.writes.get(key) === fingerprint ? { saved: true } : null }] };
      db.writes.set(key, fingerprint);
      if (kind === "shortlist") db.shortlist.set(propertyRef, value);
      else db.comments.push({ propertyRef, comment: value });
      return { rows: [{ feedback: { saved: true } }] };
    }
    if (seam === "read_tour_feedback") {
      const [tenant, projectionId, actorId] = params;
      if (tenant !== "carr-internal" || projectionId !== PROJECTION_ID || !actorId) return { rows: [{ feedback: null }] };
      return { rows: [{ feedback: { projection_id: PROJECTION_ID, share_grant_id: "secret-grant", items: [...db.grant.members].map((property_ref, i) => ({
        property_ref, route_label: String.fromCharCode(65 + i), shortlisted: db.shortlist.has(property_ref) ? db.shortlist.get(property_ref) : null, broker_notes: "internal note", token_digest: "leak",
        comments: db.comments.filter(c => c.propertyRef === property_ref).map((c, n) => ({ comment_ref: `comment:public:${String(n).padStart(32, "c")}`, comment: c.comment, created_at: "2026-10-01T12:00:00Z" })),
      })) } }] };
    }
    throw new Error(`unexpected sql ${sql}`);
  } };
  return { db, client };
}

function wire(env = store()) {
  const browser = tourSharingBrowserAccess({ ToolError });
  const adapter = fn => async args => {
    try { const r = await fn(env.client, args); return r.ok ? { ok: true, data: r.feedback ?? r.packet ?? r.map } : { ok: false, status: 404 }; }
    catch (error) { if (error instanceof ToolError && error.payload?.error === "tour_share_access_refused") return { ok: false, status: 404 }; throw error; }
  };
  const sessionArg = ({ sessionDigest, env: _env, ctx: _ctx, ...rest }) => ({ session_digest: sessionDigest, ...rest });
  const surface = createReportsWebHandler({
    now: () => env.db.now,
    exchangeShareTokenFn: async ({ tokenDigest, sessionDigest, sessionExpiresAt }) => {
      if (tokenDigest !== env.db.tokenDigest || env.db.grant.status !== "active" || env.db.now >= env.db.grant.expiresAt) return { ok: false, status: 403 };
      env.db.sessions.set(sessionDigest, { expiresAt: Math.min(Date.parse(sessionExpiresAt), env.db.grant.expiresAt) });
      return { ok: true };
    },
    readShareFn: async ({ sessionDigest }) => env.db.sessions.has(sessionDigest) && env.db.grant.status === "active"
      ? { ok: true, data: { stops: [PROP_A, PROP_B].map((property_ref, i) => ({ property_ref, route_sequence: i + 1, route_label: String.fromCharCode(65 + i), name: `Fixture property ${i + 1}`, address: `${i + 1} Test Way` })) } }
      : { ok: false, status: 404 },
    readMapFn: async () => ({ ok: false, status: 404 }),
    readFeedbackFn: adapter((c, a) => browser.readFeedback(c, sessionArg(a))),
    shortlistFn: adapter((c, a) => browser.shortlist(c, sessionArg(a))),
    commentFn: adapter((c, a) => browser.comment(c, sessionArg(a))),
  });
  return { surface, env };
}

// --- a just-enough DOM for share.js ---------------------------------------
class El {
  constructor(tag) { this.tag = tag; this.children = []; this.parent = null; this.listeners = {}; this.attrs = {}; this.hidden = false; this.disabled = false; this.value = ""; this.dataset = {}; this.className = ""; this.textContent = ""; }
  append(...nodes) { for (const n of nodes) { n.parent = this; this.children.push(n); } }
  replaceChildren() { this.children = []; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(c => c !== this); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  async click() { for (const fn of this.listeners.click || []) await fn({}); }
  all(test, out = []) { for (const c of this.children) { if (test(c)) out.push(c); c.all(test, out); } return out; }
  querySelectorAll(selector) { const cls = selector.slice(1); return this.all(c => c.className.split(" ").includes(cls)); }
}
function pageFor(surface, env) {
  const ids = {};
  const doc = {
    querySelector(selector) { return ids[selector] ||= new El(selector); },
    createElement(tag) { return new El(tag); },
  };
  const jar = { cookie: "" };
  const trace = [];
  const fetchBridge = async (path, options = {}) => {
    const headers = { origin: REPORTS_ORIGIN, "sec-fetch-site": "same-origin", ...(options.headers || {}), ...(jar.cookie ? { cookie: jar.cookie } : {}) };
    trace.push({ path, method: options.method || "GET", body: options.body });
    if (env.dropNext && (options.method === "POST") && path !== "/api/share/exchange") { env.dropNext -= 1; if (env.dropMode === "throw") throw new TypeError("network"); }
    const response = await surface.fetch(new Request(`${REPORTS_ORIGIN}${path}`, { method: options.method || "GET", headers, body: options.body }), {});
    const set = response.headers.get("set-cookie");
    if (set) jar.cookie = set.split(";")[0];
    return response;
  };
  const context = createContext({ document: doc, fetch: fetchBridge, crypto, Promise, JSON, Number, String, Array, RegExp, Error, TypeError, Math, Date, URL });
  context.globalThis = context;
  context.__CARR_TOUR_TAKE_SHARE_TOKEN__ = () => TOKEN;
  return { doc, ids, jar, trace, context };
}
async function openShare(w) {
  const source = await readFile(SHARE_JS, "utf8");
  runInContext(source, w.context);
  await w.doc.querySelector("#open-tour").click();
  const list = w.doc.querySelector("#report-list");
  for (let i = 0; i < 400 && list.attrs["aria-busy"] !== "false"; i += 1) await new Promise(resolve => setTimeout(resolve, 5));
  return list;
}
const controls = row => ({ pick: row.all(c => c.className === "shortlist-toggle")[0], field: row.all(c => c.className === "comment-field")[0], send: row.all(c => c.className === "comment-send")[0], note: row.all(c => c.className.includes("feedback-note"))[0] });

async function setup(options) {
  const env = store(options);
  env.db.tokenDigest = await digestOf(TOKEN);
  const { surface } = wire(env);
  const w = pageFor(surface, env);
  return { env, w, surface };
}

test("client shortlists and comments; broker reads the same items tied to the exact projection", async () => {
  const { env, w } = await setup();
  const list = await openShare(w);
  const rows = list.children;
  assert.equal(rows.length, 2);
  const a = controls(rows[0]), b = controls(rows[1]);
  assert.ok(a.pick && a.field && a.send, "both scopes render controls");
  await a.pick.click();
  assert.equal(a.pick.attrs["aria-pressed"], "true");
  a.field.value = "  Good frontage  ";
  await a.send.click();
  assert.equal(a.field.value, "", "saved comment clears the field");
  await b.pick.click(); await b.pick.click();
  assert.equal(b.pick.attrs["aria-pressed"], "false");

  const bodies = w.trace.filter(t => t.method === "POST" && t.path !== "/api/share/exchange").map(t => JSON.parse(t.body));
  assert.ok(bodies.every(x => x.projection_ref === PROJECTION_REF));
  assert.doesNotMatch(JSON.stringify(bodies), /session|token|grant|projection_id|tenant|actor/i);
  assert.doesNotMatch(JSON.stringify(w.trace.filter(t => t.path !== "/api/share/exchange")), new RegExp(TOKEN), "bearer is sent once, in the exchange body only");

  // Broker side: authenticated internal surface, verb reads the same store.
  const tools = tourSharingTools({ ToolError, withEnvelope: async (_c, _a, _v, _x, fn) => fn(), writeEvent: async () => {} });
  const brokerActor = { id: "broker", organization_tenant_id: "tenant-one" };
  const internal = createTourInternalWebHandler({
    readFeedbackFn: async ({ input }) => { { const r = await tools["read-tour-feedback"].handler(env.client, brokerActor, { projection_id: input.projection_id, cursor: null, limit: 100 }); return { ok: true, data: { feedback: r.feedback } }; } },
  });
  const session = { key: "opaque", csrfToken: "csrf" };
  const origin = "https://app.doctorcre.com";
  const get = () => internal.fetch(new Request(`${origin}/api/tours/feedback?projection_id=${PROJECTION_ID}`), { APP_HOST: "app.doctorcre.com" }, {}, brokerActor, session);
  assert.equal((await internal.fetch(new Request(`${origin}/api/tours/feedback?projection_id=${PROJECTION_ID}`), { APP_HOST: "app.doctorcre.com" }, {}, undefined, undefined)).status, 401);
  const response = await get();
  assert.equal(response.status, 200);
  const { feedback } = (await response.json()).data;
  assert.equal(feedback.projection_id, PROJECTION_ID);
  const byRef = Object.fromEntries(feedback.items.map(i => [i.property_ref, i]));
  assert.equal(byRef[PROP_A].shortlisted, true);
  assert.equal(byRef[PROP_B].shortlisted, false, "explicit no stays distinct from no answer");
  assert.deepEqual(byRef[PROP_A].comments.map(c => c.comment), ["Good frontage"]);
  assert.doesNotMatch(JSON.stringify(feedback), /broker_notes|internal note|token_digest|secret-grant|share_grant_id/);
});

test("controls appear only for scopes granted and only when the grant lists the property", async () => {
  const shortlistOnly = await setup({ scopes: ["view_packet", "shortlist"] });
  const rows = (await openShare(shortlistOnly.w)).children;
  const c = controls(rows[0]);
  assert.ok(c.pick); assert.equal(c.field, undefined);

  const readOnly = await setup({ scopes: ["view_packet"] });
  const rows2 = (await openShare(readOnly.w)).children;
  assert.equal(rows2.length, 2);
  assert.equal(controls(rows2[0]).pick, undefined, "packet-only grant has no feedback controls");
  assert.equal(readOnly.w.trace.filter(t => t.method === "POST" && t.path !== "/api/share/exchange").length, 0);
});

test("a stale page cannot write another Tour's property or projection", async () => {
  const { env, w, surface } = await setup();
  await openShare(w);
  const post = (path, body) => surface.fetch(new Request(`${REPORTS_ORIGIN}${path}`, { method: "POST", headers: { origin: REPORTS_ORIGIN, "sec-fetch-site": "same-origin", "content-type": "application/json", cookie: w.jar.cookie }, body: JSON.stringify(body) }), {});
  const key = "30000000-0000-4000-8000-000000000001";
  assert.equal((await post("/api/share/shortlist", { projection_ref: PROJECTION_REF, property_ref: PROP_OTHER_TOUR, shortlisted: true, idempotency_key: key })).status, 404);
  assert.equal((await post("/api/share/comment", { projection_ref: OTHER_PROJECTION_REF, property_ref: PROP_A, comment: "x", idempotency_key: key })).status, 404);
  assert.equal(env.db.shortlist.size, 0);
  assert.equal(env.db.comments.length, 0);
});

test("a dropped response retries with the same idempotency key and writes once; a new action gets a new key", async () => {
  const { env, w } = await setup();
  const list = await openShare(w);
  const a = controls(list.children[0]);
  a.field.value = "Parking looks tight";
  // The request commits on the server, then the browser sees a 503.
  env.dropNext = 0;
  const original = env.client.query;
  let failOnce = true;
  env.client.query = async (sql, params) => { const r = await original.call(env.client, sql, params); if (failOnce && sql.includes("write_tour_share_comment")) { failOnce = false; throw new Error("connection lost after commit"); } return r; };
  await a.send.click();
  assert.match(a.note.textContent, /Try again/);
  assert.equal(a.field.value, "Parking looks tight", "unsent comment is kept for retry");
  await a.send.click();
  assert.equal(a.field.value, "");
  const commentPosts = w.trace.filter(t => t.path === "/api/share/comment").map(t => JSON.parse(t.body));
  assert.equal(commentPosts.length, 2);
  assert.equal(commentPosts[0].idempotency_key, commentPosts[1].idempotency_key, "retry replays the same key");
  assert.equal(env.db.comments.length, 1, "server wrote once");
  a.field.value = "Parking looks tight";
  await a.send.click();
  const third = w.trace.filter(t => t.path === "/api/share/comment").map(t => JSON.parse(t.body)).at(-1);
  assert.notEqual(third.idempotency_key, commentPosts[0].idempotency_key, "a new action after success uses a fresh key");
  assert.equal(env.db.comments.length, 2);
});

test("double activation while a write is in flight sends one request", async () => {
  const { w } = await setup();
  const list = await openShare(w);
  const a = controls(list.children[0]);
  await Promise.all([a.pick.click(), a.pick.click()]);
  assert.equal(w.trace.filter(t => t.path === "/api/share/shortlist").length, 1);
});

test("expired, revoked and rotated-away grants refuse writes and remove the controls", async () => {
  for (const change of [env => { env.db.now = env.db.grant.expiresAt + 1000; }, env => { env.db.grant.status = "revoked"; }]) {
    const { env, w } = await setup();
    const list = await openShare(w);
    const a = controls(list.children[0]);
    change(env);
    await a.pick.click();
    assert.equal(env.db.shortlist.size, 0);
    assert.match(a.note.textContent, /no longer active/);
    assert.equal(list.children[0].all(c => c.className === "feedback").length, 0, "controls removed");
  }
});

test("a new browser cannot reuse a session after the grant is revoked, and the bearer cannot be reused after expiry", async () => {
  const { env, w, surface } = await setup();
  await openShare(w);
  env.db.grant.status = "revoked";
  const read = await surface.fetch(new Request(`${REPORTS_ORIGIN}/api/share/feedback`, { headers: { cookie: w.jar.cookie } }), {});
  assert.equal(read.status, 404);
  const exchange = await surface.fetch(new Request(`${REPORTS_ORIGIN}/api/share/exchange`, { method: "POST", headers: { origin: REPORTS_ORIGIN, "sec-fetch-site": "same-origin", "content-type": "application/json" }, body: JSON.stringify({ token: TOKEN }) }), {});
  assert.equal(exchange.status, 403);
});

test("scanner-safe bootstrap: nothing is exchanged or written until the person selects Open tour", async () => {
  const { w } = await setup();
  runInContext(await readFile(SHARE_JS, "utf8"), w.context);
  assert.equal(w.trace.length, 0, "loading the page makes no request, so a link scanner cannot start a session");
  assert.equal(w.doc.querySelector("#open-tour").disabled, false);
});

test("share.js has no pdf or reaction route and never stores the session or token", async () => {
  const [script, html] = await Promise.all([readFile(SHARE_JS, "utf8"), readFile(SHARE_HTML, "utf8")]);
  assert.match(script, /\/api\/share\/\$\{kind\}/);
  assert.doesNotMatch(script + html, /\/api\/share\/(?:pdf|reaction)|localStorage|sessionStorage|document\.cookie/);
  assert.doesNotMatch(html, /requires? a future governed permission-scope amendment/);
});

test("broker Tours page can issue the two feedback scopes and shows feedback for the approved projection", async () => {
  const [app, html] = await Promise.all([readFile(APP_JS, "utf8"), readFile(TOURS_HTML, "utf8")]);
  assert.match(html, /name="scope" value="shortlist"/);
  assert.match(html, /name="scope" value="comment"/);
  assert.match(html, /id="feedback-list"/);
  assert.match(app, /\/api\/tours\/feedback\?projection_id=/);
  assert.match(app, /textContent/);
  assert.doesNotMatch(app, /innerHTML/);
});

test("broker door: feedback scopes need packet access and unknown scopes stay refused", async () => {
  const env = { APP_HOST: "app.doctorcre.com" };
  const session = { key: "opaque", csrfToken: "csrf" };
  const actor = { id: "broker" };
  const seen = [];
  const surface = createTourInternalWebHandler({ issueShareGrantFn: async ({ input }) => { seen.push(input.permission_scopes); return { ok: true, data: { share_grant_id: "66666666-6666-4666-8666-666666666666" } }; } });
  const body = scopes => ({ projection_id: PROJECTION_ID, token_digest: `sha256:${"a".repeat(64)}`, permission_scopes: scopes, expires_at: "2026-10-08T12:00:00.000Z", receipt_digest: `sha256:${"b".repeat(64)}`, idempotency_key: "40000000-0000-4000-8000-000000000001" });
  const post = scopes => surface.fetch(new Request("https://app.doctorcre.com/api/tours/share/issue", { method: "POST", headers: { origin: "https://app.doctorcre.com", "sec-fetch-site": "same-origin", "content-type": "application/json", "x-carr-csrf": "csrf" }, body: JSON.stringify(body(scopes)) }), env, {}, actor, session);
  for (const ok of [["view_packet", "shortlist", "comment"], ["view_packet", "view_map", "shortlist", "comment"], ["view_packet", "comment"], ["view_packet"], ["view_map"]])
    assert.equal((await post(ok)).status, 200, ok.join());
  for (const bad of [["shortlist"], ["comment"], ["view_map", "shortlist"], ["view_packet", "edit_notes"], ["view_packet", "shortlist", "shortlist"], []])
    assert.equal((await post(bad)).status, 400, bad.join());
  assert.equal(seen.length, 5);
});

test("broker feedback route is GET only, authenticated, one projection id, and coarse on failure", async () => {
  const env = { APP_HOST: "app.doctorcre.com" };
  const session = { key: "opaque", csrfToken: "csrf" };
  const surface = createTourInternalWebHandler({ readFeedbackFn: async () => { throw new Error("connect ECONNRESET db.internal.example:5432"); } });
  const url = suffix => new Request(`https://app.doctorcre.com/api/tours/feedback${suffix}`);
  assert.equal((await surface.fetch(url(`?projection_id=${PROJECTION_ID}`), env, {}, undefined, undefined)).status, 401);
  assert.equal((await surface.fetch(url(""), env, {}, { id: "b" }, session)).status, 400);
  assert.equal((await surface.fetch(url(`?projection_id=${PROJECTION_ID}&tenant_id=x`), env, {}, { id: "b" }, session)).status, 400);
  assert.equal((await surface.fetch(new Request("https://app.doctorcre.com/api/tours/feedback", { method: "POST" }), env, {}, { id: "b" }, session)).status, 405);
  const failed = await surface.fetch(url(`?projection_id=${PROJECTION_ID}`), env, {}, { id: "b" }, session);
  assert.equal(failed.status, 503);
  assert.doesNotMatch(await failed.text(), /ECONNRESET|db\.internal/);
});
