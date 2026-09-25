import test from "node:test";
import assert from "node:assert/strict";
import { recordHomeSeam, lifecycleSeam, healthSeam } from "../js/capability-seams.js";

test("recordHomeSeam (V5-F01) is honestly unavailable when the client has no getDeal", async () => {
  const result = await recordHomeSeam({}, "d1");
  assert.equal(result.available, false);
  assert.equal(result.capability, "V5-F01");
  assert.match(result.reason, /not deployed/);
});

test("recordHomeSeam is available and returns real client data when getDeal exists", async () => {
  const client = { getDeal: async (id) => ({ deal: { id, name: "Acme" } }) };
  const result = await recordHomeSeam(client, "d1");
  assert.equal(result.available, true);
  assert.equal(result.detail.deal.name, "Acme");
});

test("recordHomeSeam refuses honestly, never fabricating a value, when the real call throws", async () => {
  const client = { getDeal: async () => { throw new Error("unauthorized"); } };
  const result = await recordHomeSeam(client, "d1");
  assert.equal(result.available, false);
  assert.match(result.reason, /unauthorized/);
});

test("lifecycleSeam (V5-J102) is unavailable today — the client has no typed transition yet", async () => {
  const client = { setNextStep: async () => ({ status: "ok" }) }; // only the generic write exists
  const result = await lifecycleSeam(client, "d1");
  assert.equal(result.available, false);
  assert.equal(result.capability, "V5-J102");
  assert.match(result.reason, /not deployed yet/);
});

test("lifecycleSeam becomes available the moment a client implements transitionLifecycle", async () => {
  const client = { transitionLifecycle: async ({ deal }) => ({ deal, phase: "Legal" }) };
  const result = await lifecycleSeam(client, "d1");
  assert.equal(result.available, true);
  assert.equal(result.detail.phase, "Legal");
});

test("healthSeam (V5-A01) is honestly unavailable without a client getHealth", async () => {
  const result = await healthSeam({});
  assert.equal(result.available, false);
  assert.equal(result.capability, "V5-A01");
});

test("healthSeam reports real health when the client answers", async () => {
  const client = { getHealth: async () => ({ status: "green" }) };
  const result = await healthSeam(client);
  assert.equal(result.available, true);
  assert.equal(result.detail.status, "green");
});

test("every seam is honest about a missing or undefined client, not just an empty object", async () => {
  assert.equal((await recordHomeSeam(undefined, "d1")).available, false);
  assert.equal((await lifecycleSeam(null, "d1")).available, false);
  assert.equal((await healthSeam(undefined)).available, false);
});
