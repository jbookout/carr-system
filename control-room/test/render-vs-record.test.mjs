// RENDER VERSUS RECORD
//
// Every other test in this suite asserts that things EXIST. None asserts that a
// displayed value MATCHES the record behind it. That gap is why 94 passing tests
// caught none of the three defects below, all of which uncoached human testing
// found in a single morning on 2026-08-13.
//
// The defect class: a fixture holds a field the renderer never displays, so the
// interface silently omits — or worse, misreports — data the system actually has.
//
// The worst instance is the plan-approval surface, which prints "Unknown" for a
// sponsor and capability the fixture does hold, because the renderer reads keys
// that do not exist. That is not a cosmetic miss. The whole product leans on
// Unknown meaning "the record lacks this value", and the honest-health journey
// PASSED on exactly that promise. Once Unknown can also mean "the renderer asked
// for the wrong key", no Unknown anywhere can be trusted.
//
// These tests read app.js as source rather than executing it, because the render
// functions build DOM nodes and there is no DOM under node. Each test scopes its
// assertion to the single render function that owns the surface, so a reference
// living on some other surface cannot satisfy it — which is precisely how two of
// these defects hid.

import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SOURCE = fs.readFileSync(path.join(ROOT, "public", "js", "app.js"), "utf8");

const fixture = name =>
  JSON.parse(fs.readFileSync(path.join(ROOT, "fixtures", `${name}.v1.json`), "utf8"));

// Return the body of one named render function. Scoping matters: the actuator
// name IS rendered somewhere in this file, just not on the surface that needs it,
// and a whole-file search would therefore report a defect as fixed.
function renderBody(fnName) {
  const start = SOURCE.indexOf(`function ${fnName}(`);
  assert.notEqual(start, -1, `render function ${fnName} not found in app.js`);
  const next = SOURCE.slice(start + 1).search(/\nfunction |\nconst renderers/);
  return SOURCE.slice(start, next === -1 ? SOURCE.length : start + 1 + next);
}

// The surface payload lives at the fixture's top-level `data`, NOT under
// scenarios.normal, which carries the authored presentation states. Getting this
// wrong made all three tests crash with a TypeError on the first run, which read
// as three caught defects and was in fact one bug in this file.
const payloadOf = surface => fixture(surface).data;

test("service surface renders the configuration fingerprint it holds per environment", () => {
  // fixtures/service.v1.json gives every environment a configuration_fingerprint.
  // The environment comparison table header is Environment, State, Version,
  // Schema, Source, Freshness, Last verified — no Configuration column — so the
  // field is dropped at render. It surfaces only in the Deployment Release panel,
  // for one release, which cannot answer the per-environment question this
  // surface exists to answer.
  const environments = payloadOf("service").environments;
  assert.ok(environments.length > 0, "fixture should define environments");
  assert.ok(
    environments.some(env => "configuration_fingerprint" in env),
    "fixture should carry configuration_fingerprint (if this fails the defect moved)"
  );

  const body = renderBody("renderService");
  assert.match(
    body,
    /configuration_fingerprint/,
    "renderService never reads configuration_fingerprint, so configuration is invisible " +
      "on the only surface that compares environments side by side"
  );
});

test("plan-approval surface reads only session_scope keys that exist in the record", () => {
  // THE GENERAL GUARD. This is the test that would have caught the Unknown defect
  // before a human ever saw it, and it keeps catching the whole class: any key the
  // renderer reads off session_scope must actually be present in the fixture.
  // Today the renderer asks for .sponsor and .capability; the fixture has
  // sponsoring_human_id and session_capability_profile. Both reads resolve to
  // undefined and print as Unknown.
  const scope = payloadOf("plan-approval").session_scope;
  const present = new Set(Object.keys(scope));

  const body = renderBody("renderPlan");
  const read = [...body.matchAll(/data\.session_scope\.([A-Za-z_][A-Za-z0-9_]*)/g)].map(m => m[1]);
  assert.ok(read.length > 0, "renderPlan should read session_scope at all");

  const missing = [...new Set(read)].filter(key => !present.has(key));
  assert.deepEqual(
    missing,
    [],
    `renderPlan reads session_scope keys the record does not have: ${missing.join(", ")}. ` +
      `The record holds: ${[...present].join(", ")}. Each missing key renders as "Unknown", ` +
      `which is indistinguishable from an honest "the record lacks this value".`
  );
});

test("deployment surface names the actuator its own fixture holds", () => {
  // fixtures/deployment.v1.json carries actuator {name, registered, invocation,
  // phase0_mode}. renderDeployment never displays it. The only actuator panel in
  // the product belongs to renderService and reads a different field entirely
  // (data.actuators, plural), so the deployment journey could satisfy "sanctioned
  // actuator is named" only by way of the audit screen — discoverable in
  // principle, and not on the path in practice.
  const deployment = payloadOf("deployment");
  assert.ok(deployment.actuator?.name, "fixture should carry actuator.name");

  const body = renderBody("renderDeployment");
  assert.match(
    body,
    /\.actuator\b/,
    "renderDeployment never reads .actuator, so the sanctioned actuator is not named " +
      "on the surface where a release is actually inspected"
  );
});

test("every surface renderer reads its fixture's top-level payload key", () => {
  // A cheap breadth check against the same failure mode on surfaces nobody has
  // exercised by hand yet. It asserts only that each renderer touches the payload
  // it was given; it deliberately does not assert field-level coverage, which is
  // what the three targeted tests above do for the cases we know about.
  const surfaces = {
    overview: "renderOverview",
    service: "renderService",
    "work-request": "renderWorkRequest",
    "plan-approval": "renderPlan",
    deployment: "renderDeployment",
    incident: "renderIncident",
    audit: "renderAudit",
  };
  for (const [surface, fn] of Object.entries(surfaces)) {
    const payload = payloadOf(surface);
    const keys = Object.keys(payload).filter(k => k !== "freshness" && k !== "message");
    if (keys.length === 0) continue;
    const body = renderBody(fn);
    assert.ok(
      keys.some(k => body.includes(k)) || body.includes("data."),
      `${fn} does not appear to read any of its fixture's payload keys: ${keys.join(", ")}`
    );
  }
});
