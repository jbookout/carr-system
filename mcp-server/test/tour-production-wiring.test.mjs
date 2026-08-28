import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { TOOLS } from "../src/tools.js";
import { projectTourDetail, projectTourLibrary } from "../src/tour-runtime.js";

const root = path.resolve(import.meta.dirname, "../..");
const read = file => fs.readFileSync(path.join(root, file), "utf8");

test("production tool registry contains every Tour delivery verb", () => {
  for (const name of [
    "search-tour-properties", "append-tour-selection-cart-version", "read-tour-selection-cart",
    "prepare-tour-route-version", "issue-tour-share-grant", "rotate-tour-share-grant",
    "revoke-tour-share-grant", "read-tour-sharing-library", "request-tour-pdf-render",
    "read-tour-pdf-render", "record-tour-pdf-render-result", "record-tour-pdf-human-review",
  ]) assert.ok(TOOLS[name], name);
});

test("Worker production router mounts authenticated Tours and public reports", () => {
  const index = read("mcp-server/src/index.js");
  const dealroom = read("mcp-server/src/dealroom-web.js");
  assert.match(index, /createTourInternalWebHandler\(createTourRuntimeAdapters\(\)\)/);
  assert.match(index, /createReportsWebHandler\(createReportsRuntimeAdapters\(\)\)/);
  assert.match(index, /isReportsHostRequest\(request\)[\s\S]*reportsHandler\.fetch/);
  assert.ok(index.indexOf("isReportsHostRequest(request)") < index.indexOf('url.pathname === "/mcp"'), "reports host must close before machine/OAuth routing");
  assert.match(index, /tourHandler:\s*tourInternalHandler/);
  assert.match(dealroom, /isTourInternalRequest\(request\)[\s\S]*tourHandler\.fetch/);
  assert.match(dealroom, /"\/api\/tours\/"/);
  assert.match(dealroom, /"\/tours\/"/);
});

test("production Tour runtime preserves tool envelopes and digest-only public access", () => {
  const runtime = read("mcp-server/src/tour-runtime.js");
  assert.match(runtime, /callTool\(\{ \.\.\.env, ctx \}, actor, verb, args\)/);
  assert.match(runtime, /tourSharingBrowserAccess/);
  assert.match(runtime, /sharing\.exchange/);
  assert.match(runtime, /sharing\.readPacket/);
  assert.match(runtime, /sharing\.readMap/);
  assert.match(runtime, /read_tour_projection_creation_metadata/);
  assert.match(runtime, /derivedIdempotencyUuid\("tour-pdf-render-result", context\.input\.idempotency_key\)/);
  assert.doesNotMatch(runtime, /record-tour-pdf-render-result[\s\S]{0,300}crypto\.randomUUID\(\)/);
  assert.doesNotMatch(runtime, /from ops\.tour_public_projection/i);
  assert.doesNotMatch(runtime, /raw_token|plaintext_token|authorization\s*:/i);
});

test("production Tour runtime presents the exact browser view without promoting drafts", () => {
  assert.deepEqual(projectTourLibrary({ tours: [{ id: "tour", tour_name: "Bay County", tour_status: "draft" }] }), {
    tours: [{ id: "tour", tour_name: "Bay County", tour_status: "draft", name: "Bay County", status: "draft" }],
  });
  const detail = projectTourDetail({
    id: "tour", tour_name: "Bay County", tour_status: "draft", route_version: 1,
    routes: [
      { id: "draft-route", route_version: 2, accepted: false, stops: [{ id: "stop", route_sequence: 1, route_label: "A" }] },
      { id: "accepted-route", route_version: 1, accepted: true, stops: [] },
    ],
    projections: [{ id: "draft-projection", status: "draft" }, { id: "approved-projection", status: "approved" }],
    shares: [
      { share_grant_id: "revoked-share", projection_id: "approved-projection", status: "revoked" },
      { share_grant_id: "active-share", projection_id: "approved-projection", status: "active" },
    ],
  });
  assert.equal(detail.name, "Bay County");
  assert.equal(detail.route_version_id, "draft-route");
  assert.equal(detail.route_version_state, "draft");
  assert.equal(detail.accepted_route_version, 1);
  assert.equal(detail.stops[0].label, "A");
  assert.equal(detail.projection_id, "approved-projection", "draft projections never become share authority");
  assert.equal(detail.share_grant_id, "active-share");
});
